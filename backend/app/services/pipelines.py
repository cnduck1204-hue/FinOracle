"""检索方案的查询与构建：列出可选方案，为版本建立附加方案的索引。"""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import utc_now
from app.models.document import DEFAULT_INGEST, DocumentVersion
from app.models.import_job import ImportJob
from app.models.user import User
from app.rag.pipelines import (
    Pipeline,
    PipelineRegistry,
    SourceFile,
    pipeline_unavailable,
    source_unsupported,
)
from app.repositories.documents import (
    DocumentVersionRepository,
    VersionIndexRepository,
)
from app.repositories.import_jobs import ImportJobRepository
from app.schemas.ingestion import JobResponse
from app.schemas.pipeline import PipelineResponse, VersionPipelineResponse
from app.services.document_errors import version_not_found, version_state_conflict
from app.services.document_imports import lock_idle_version
from app.services.document_library import job_response

# 默认方案的索引就绪后才能建立附加索引，避免与解析、向量化同时运行
_BUILDABLE = frozenset({"ready", "published"})


def _source(version: DocumentVersion) -> SourceFile:
    return SourceFile(
        document_id=version.document_id,
        version_id=version.id,
        object_key=version.object_key,
        file_type=version.file_type,
        parse_options=dict(version.parse_options),
        pdf_object_key=version.pdf_object_key,
    )


def _describe(pipeline: Pipeline, registry: PipelineRegistry) -> PipelineResponse:
    return PipelineResponse(
        name=pipeline.name,
        label=pipeline.label,
        description=pipeline.description,
        ingest=pipeline.ingest.name,
        channels=sorted(pipeline.retrieval.requires),
        default=pipeline.name == registry.default.name,
        available=pipeline.unavailable_reason is None,
        unavailable_reason=pipeline.unavailable_reason,
    )


def list_pipelines(registry: PipelineRegistry) -> list[PipelineResponse]:
    return [_describe(pipeline, registry) for pipeline in registry.all()]


async def list_for_version(
    version_id: UUID, session: AsyncSession, registry: PipelineRegistry
) -> list[VersionPipelineResponse]:
    async with session.begin():
        version = await DocumentVersionRepository(session).get(version_id)
        if version is None:
            raise version_not_found()
        indexes = {
            index.ingest: index
            for index in await VersionIndexRepository(session).list_for_version(
                version_id
            )
        }
        source = _source(version)
        result = []
        for pipeline in registry.all():
            name = pipeline.ingest.name
            supported = pipeline.ingest.parser.accepts(source)
            if name == DEFAULT_INGEST:
                indexed = version.indexed_at is not None
                result.append(
                    VersionPipelineResponse(
                        pipeline=_describe(pipeline, registry),
                        status="ready" if indexed else "not_built",
                        supported=supported,
                        unit_count=version.chunk_count,
                        page_count=version.page_count,
                        index_model=version.index_model,
                        indexed_at=version.indexed_at,
                    )
                )
                continue
            index = indexes.get(name)
            result.append(
                VersionPipelineResponse(
                    pipeline=_describe(pipeline, registry),
                    status=index.status if index else "not_built",
                    supported=supported,
                    unit_count=index.unit_count if index else 0,
                    page_count=index.page_count if index else None,
                    index_model=index.index_model if index else None,
                    indexed_at=index.indexed_at if index else None,
                )
            )
        return result


async def build_index(
    version_id: UUID,
    name: str,
    user: User,
    session: AsyncSession,
    registry: PipelineRegistry,
) -> JobResponse:
    """为版本创建附加方案的入库任务；可重复调用以重建。"""
    pipeline = registry.get(name)
    if pipeline.unavailable_reason is not None:
        raise pipeline_unavailable(pipeline.unavailable_reason)
    if pipeline.ingest.name == DEFAULT_INGEST:
        raise version_state_conflict("默认方案随版本入库，请使用批准或重新解析")
    async with session.begin():
        version = await lock_idle_version(session, version_id)
        if version.status not in _BUILDABLE:
            raise version_state_conflict("版本完成向量化后才能建立其他方案的索引")
        if not pipeline.ingest.parser.accepts(_source(version)):
            raise source_unsupported()
        index = await VersionIndexRepository(session).ensure(
            version_id, pipeline.ingest.name
        )
        index.status = "building"
        job = ImportJob(
            version_id=version_id,
            kind="pipeline",
            ingest=pipeline.ingest.name,
            run_after=utc_now(),
            created_by=user.id,
        )
        jobs = ImportJobRepository(session)
        await jobs.add(job)
        return job_response(await jobs.get_with_context(job.id))
