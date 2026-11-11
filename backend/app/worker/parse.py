"""解析任务：调用默认方案的解析器，保存切片并更新版本。

成功后版本进入 parsed（待校验），失败时为 failed。结果写入前锁定任务确认归属，切片替换、
版本更新与任务完成在同一事务中提交。
"""

from collections.abc import Callable

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import ParsingSettings
from app.models.import_job import ImportJob
from app.rag.parsing.converters import ConverterProvider
from app.rag.parsing.providers import create_document_converter
from app.rag.pipelines.base import ParsedUnits, SourceFile, UnitParser
from app.rag.pipelines.structured import StructuredParser
from app.repositories.documents import (
    DocumentChunkRepository,
    DocumentVersionRepository,
)
from app.repositories.import_jobs import ImportJobRepository
from app.services.document_errors import version_not_found
from app.worker.runtime import JobContext


class ParseHandler:
    kind = "parse"

    def __init__(
        self,
        settings: ParsingSettings,
        converter_factory: Callable[
            [ParsingSettings], ConverterProvider
        ] = create_document_converter,
        *,
        auto_approve: bool = False,
        parser: UnitParser | None = None,
    ) -> None:
        self.parser = parser or StructuredParser(settings, converter_factory)
        # 为 True 时，完整解析（没有待 OCR 图片）的版本直接进入向量化，无需人工批准
        self.auto_approve = auto_approve

    async def run(self, context: JobContext) -> None:
        source = await self._start(context)
        parsed = await self.parser.parse(context, source)
        await self._save(context, parsed)

    async def on_failure(self, session: AsyncSession, context: JobContext) -> None:
        version = await DocumentVersionRepository(session).get(
            context.job.version_id, for_update=True
        )
        if version is not None:
            version.status = "failed"

    async def _start(self, context: JobContext) -> SourceFile:
        async with context.sessions() as session, session.begin():
            await context.lock_job(session)
            version = await DocumentVersionRepository(session).get(
                context.job.version_id, for_update=True
            )
            if version is None:
                raise version_not_found()
            version.status = "parsing"
            return SourceFile(
                document_id=version.document_id,
                version_id=version.id,
                object_key=version.object_key,
                file_type=version.file_type,
                parse_options=dict(version.parse_options),
            )

    async def _save(self, context: JobContext, parsed: ParsedUnits) -> None:
        async with context.sessions() as session, session.begin():
            job = await context.lock_job(session)
            version = await DocumentVersionRepository(session).get(
                context.job.version_id, for_update=True
            )
            if version is None:
                raise version_not_found()
            await DocumentChunkRepository(session).replace_for_version(
                version, parsed.chunks
            )
            version.status = "parsed"
            version.parse_status = parsed.status
            version.page_count = parsed.page_count
            version.parse_stats = parsed.stats
            version.parse_warnings = parsed.warnings
            version.parser_version = parsed.parser_version
            version.pdf_object_key = parsed.pdf_object_key
            if self.auto_approve and parsed.status == "completed":
                version.status = "indexing"
                await ImportJobRepository(session).add(
                    ImportJob(
                        version_id=version.id,
                        kind="index",
                        run_after=context.now(),
                        created_by=context.job.created_by,
                    )
                )
            context.succeed(job)
