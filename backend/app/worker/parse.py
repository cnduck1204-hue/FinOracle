"""解析任务：下载原件 → 按需转换为 PDF 并保存 → 解析、OCR、切片 → 保存文本块与切片。

成功后版本进入 parsed（待校验），失败时为 failed。结果写入前锁定任务确认归属，切片替换、
版本更新与任务完成在同一事务中提交。
"""

import asyncio
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

from pydantic import TypeAdapter, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import ParsingSettings
from app.rag.parsing import (
    PARSER_VERSION,
    ParseResult,
    TextBlock,
    convert_to_pdf,
    parse_file,
)
from app.rag.parsing.converters import CONVERTIBLE_TYPES, ConverterProvider
from app.repositories.documents import (
    DocumentChunkRepository,
    DocumentVersionRepository,
)
from app.schemas.document import ParseRequest
from app.services.document_errors import (
    document_empty,
    file_missing,
    invalid_parse_options,
    version_not_found,
)
from app.services.document_parsing import (
    create_document_converter,
    ocr_provider,
    parse_options,
)
from app.storage import ObjectStorage
from app.storage.keys import blocks_key, converted_pdf_key
from app.worker.runtime import JobContext

_BLOCKS = TypeAdapter(list[TextBlock])


@dataclass(frozen=True, slots=True)
class _Source:
    document_id: UUID
    object_key: str
    file_type: str
    parse_options: dict[str, Any]


class ParseHandler:
    kind = "parse"

    def __init__(
        self,
        settings: ParsingSettings,
        converter_factory: Callable[
            [ParsingSettings], ConverterProvider
        ] = create_document_converter,
    ) -> None:
        self.settings = settings
        self.converter_factory = converter_factory

    async def run(self, context: JobContext) -> None:
        source = await self._start(context)
        try:
            request = ParseRequest.model_validate(source.parse_options)
        except ValidationError as error:
            raise invalid_parse_options() from error

        version_id = context.job.version_id
        pdf_key: str | None = None
        with tempfile.TemporaryDirectory(prefix="finoracle-parse-") as directory:
            workdir = Path(directory)
            context.report("downloading")
            original = workdir / f"original.{source.file_type}"
            await asyncio.to_thread(
                _download, context.storage, source.object_key, original
            )
            document = original
            if source.file_type in CONVERTIBLE_TYPES:
                context.report("converting")
                document = await asyncio.to_thread(
                    convert_to_pdf,
                    original,
                    self.converter_factory(self.settings),
                    workdir,
                )
                pdf_key = converted_pdf_key(source.document_id, version_id)
                await asyncio.to_thread(
                    context.storage.put_file,
                    pdf_key,
                    document,
                    content_type="application/pdf",
                )
            result = await asyncio.to_thread(
                parse_file,
                document,
                ocr=ocr_provider(request.ocr),
                chunker=request.chunker,
                options=parse_options(request),
                progress=context.report,
            )
            if result.status == "empty":
                raise document_empty()
            context.report("saving")
            blocks = workdir / "blocks.json"
            blocks.write_bytes(_BLOCKS.dump_json(result.blocks or []))
            await asyncio.to_thread(
                context.storage.put_file,
                blocks_key(source.document_id, version_id),
                blocks,
                content_type="application/json",
            )
        await self._save(context, result, pdf_key)

    async def on_failure(self, session: AsyncSession, context: JobContext) -> None:
        version = await DocumentVersionRepository(session).get(
            context.job.version_id, for_update=True
        )
        if version is not None:
            version.status = "failed"

    async def _start(self, context: JobContext) -> _Source:
        async with context.sessions() as session, session.begin():
            await context.lock_job(session)
            version = await DocumentVersionRepository(session).get(
                context.job.version_id, for_update=True
            )
            if version is None:
                raise version_not_found()
            version.status = "parsing"
            return _Source(
                document_id=version.document_id,
                object_key=version.object_key,
                file_type=version.file_type,
                parse_options=dict(version.parse_options),
            )

    async def _save(
        self, context: JobContext, result: ParseResult, pdf_key: str | None
    ) -> None:
        async with context.sessions() as session, session.begin():
            job = await context.lock_job(session)
            version = await DocumentVersionRepository(session).get(
                context.job.version_id, for_update=True
            )
            if version is None:
                raise version_not_found()
            await DocumentChunkRepository(session).replace_for_version(
                version, result.chunks
            )
            version.status = "parsed"
            version.parse_status = result.status
            version.page_count = result.page_count
            version.parse_stats = {
                **result.stats.model_dump(),
                # auto 时实际采用的切片方式与 OCR 实现
                "chunker": result.chunker,
                "ocr_provider": result.ocr_provider,
            }
            version.parse_warnings = result.warnings
            version.parser_version = PARSER_VERSION
            version.pdf_object_key = pdf_key
            context.succeed(job)


def _download(storage: ObjectStorage, key: str, path: Path) -> None:
    try:
        storage.download_file(key, path)
    except FileNotFoundError as error:
        raise file_missing() from error
