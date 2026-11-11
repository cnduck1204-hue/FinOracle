"""附加方案的入库任务：解析、写入切片、建立索引，状态记录在 version_indexes。

默认方案走版本自身的生命周期（解析、批准、向量化、发布），见 parse.py 与 index.py。
附加方案不改变版本状态，失败也不影响默认方案和已发布的内容。
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.rag.pipelines import (
    IndexTarget,
    IngestPipeline,
    ParsedUnits,
    PipelineRegistry,
    SourceFile,
    StoredUnit,
)
from app.repositories.documents import (
    DocumentChunkRepository,
    DocumentRepository,
    DocumentVersionRepository,
    VersionIndexRepository,
)
from app.services.document_errors import version_not_found
from app.worker.runtime import JobContext


class PipelineHandler:
    kind = "pipeline"

    def __init__(self, pipelines: PipelineRegistry) -> None:
        self.pipelines = pipelines

    async def run(self, context: JobContext) -> None:
        ingest = self.pipelines.ingest(context.job.ingest)
        source = await self._start(context, ingest)
        parsed = await ingest.parser.parse(context, source)
        target, units = await self._save_units(context, ingest, source, parsed)
        await ingest.build(context, target, units)
        await self._finish(context, ingest, parsed, len(units))

    async def on_failure(self, session: AsyncSession, context: JobContext) -> None:
        index = await VersionIndexRepository(session).get(
            context.job.version_id, context.job.ingest or "", for_update=True
        )
        if index is not None:
            index.status = "failed"

    async def _start(self, context: JobContext, ingest: IngestPipeline) -> SourceFile:
        async with context.sessions() as session, session.begin():
            await context.lock_job(session)
            version = await DocumentVersionRepository(session).get(
                context.job.version_id, for_update=True
            )
            if version is None:
                raise version_not_found()
            index = await VersionIndexRepository(session).ensure(
                version.id, ingest.name
            )
            index.status = "building"
            return SourceFile(
                document_id=version.document_id,
                version_id=version.id,
                object_key=version.object_key,
                file_type=version.file_type,
                parse_options=dict(version.parse_options),
                pdf_object_key=version.pdf_object_key,
            )

    async def _save_units(
        self,
        context: JobContext,
        ingest: IngestPipeline,
        source: SourceFile,
        parsed: ParsedUnits,
    ) -> tuple[IndexTarget, list[StoredUnit]]:
        async with context.sessions() as session, session.begin():
            await context.lock_job(session)
            version = await DocumentVersionRepository(session).get(
                context.job.version_id, for_update=True
            )
            if version is None:
                raise version_not_found()
            document = await DocumentRepository(session).get(version.document_id)
            chunks = DocumentChunkRepository(session)
            await chunks.replace_for_version(version, parsed.chunks, ingest=ingest.name)
            rows = await chunks.all_for_version(version.id, ingest=ingest.name)
            target = IndexTarget(
                document_id=version.document_id,
                version_id=version.id,
                title=document.title,
                pdf_key=source.pdf_key,
            )
            return target, [StoredUnit.from_row(row) for row in rows]

    async def _finish(
        self,
        context: JobContext,
        ingest: IngestPipeline,
        parsed: ParsedUnits,
        unit_count: int,
    ) -> None:
        async with context.sessions() as session, session.begin():
            job = await context.lock_job(session)
            index = await VersionIndexRepository(session).ensure(
                context.job.version_id, ingest.name
            )
            index.status = "ready"
            index.unit_count = unit_count
            index.page_count = parsed.page_count
            index.parser_version = parsed.parser_version
            index.stats = parsed.stats
            index.index_model = ingest.model_id
            index.indexed_at = context.now()
            context.succeed(job)
