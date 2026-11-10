"""结构化解析器：转换、解析、OCR、按条文切片。支持全部上传格式。"""

import asyncio
import tempfile
from collections.abc import Callable
from pathlib import Path

from pydantic import TypeAdapter, ValidationError

from app.core.config import ParsingSettings
from app.rag.parsing import PARSER_VERSION, TextBlock, convert_to_pdf, parse_file
from app.rag.parsing.converters import CONVERTIBLE_TYPES, ConverterProvider
from app.rag.pipelines.base import IngestRun, ParsedUnits, SourceFile
from app.schemas.document import ParseRequest
from app.services.document_errors import (
    document_empty,
    file_missing,
    invalid_parse_options,
)
from app.services.document_parsing import (
    create_document_converter,
    ocr_provider,
    parse_options,
)
from app.storage import ObjectStorage
from app.storage.keys import blocks_key, converted_pdf_key

_BLOCKS = TypeAdapter(list[TextBlock])


def download(storage: ObjectStorage, key: str, path: Path) -> None:
    try:
        storage.download_file(key, path)
    except FileNotFoundError as error:
        raise file_missing() from error


class StructuredParser:
    def __init__(
        self,
        settings: ParsingSettings,
        converter_factory: Callable[
            [ParsingSettings], ConverterProvider
        ] = create_document_converter,
    ) -> None:
        self.settings = settings
        self.converter_factory = converter_factory

    def accepts(self, source: SourceFile) -> bool:
        return True

    async def parse(self, run: IngestRun, source: SourceFile) -> ParsedUnits:
        try:
            request = ParseRequest.model_validate(source.parse_options)
        except ValidationError as error:
            raise invalid_parse_options() from error

        pdf_key: str | None = None
        with tempfile.TemporaryDirectory(prefix="finoracle-parse-") as directory:
            workdir = Path(directory)
            run.report("downloading")
            original = workdir / f"original.{source.file_type}"
            await asyncio.to_thread(download, run.storage, source.object_key, original)
            document = original
            if source.file_type in CONVERTIBLE_TYPES:
                run.report("converting")
                document = await asyncio.to_thread(
                    convert_to_pdf,
                    original,
                    self.converter_factory(self.settings),
                    workdir,
                )
                pdf_key = converted_pdf_key(source.document_id, source.version_id)
                await run.run_owned(
                    run.storage.put_file,
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
                progress=run.report,
            )
            if result.status == "empty":
                raise document_empty()
            run.report("saving")
            blocks = workdir / "blocks.json"
            blocks.write_bytes(_BLOCKS.dump_json(result.blocks or []))
            await run.run_owned(
                run.storage.put_file,
                blocks_key(source.document_id, source.version_id),
                blocks,
                content_type="application/json",
            )
        return ParsedUnits(
            chunks=result.chunks,
            status=result.status,
            page_count=result.page_count,
            stats={
                **result.stats.model_dump(),
                # auto 时实际采用的切片方式与 OCR 实现
                "chunker": result.chunker,
                "ocr_provider": result.ocr_provider,
            },
            warnings=result.warnings,
            parser_version=PARSER_VERSION,
            pdf_object_key=pdf_key,
        )
