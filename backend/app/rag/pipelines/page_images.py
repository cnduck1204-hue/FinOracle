"""万物转图片：任意上传格式都转成页面图片，图片存入对象存储并作为检索结果返回。

不抽取文字，一页是一个切片（正文为空、位置为整页）。.doc 等沿用默认方案转换好的 PDF；
.docx 与文本文件经 Gotenberg 转为 PDF，图片文件直接规范化为 PNG。
"""

import asyncio
import tempfile
from collections.abc import Callable
from pathlib import Path

from app.core.config import ParsingSettings
from app.rag.files import download
from app.rag.indexing.visual_index import StoredPageIndexer as StoredPageIndexer
from app.rag.models import IngestRun, ParsedUnits, SourceFile
from app.rag.parsing.converters import ConverterProvider
from app.rag.parsing.errors import document_empty
from app.rag.parsing.pages import Page, normalize_image, render_pdf
from app.rag.parsing.parsers.text import decode_text
from app.rag.parsing.providers import create_document_converter
from app.rag.pipelines.pdf_blocks import BlockChunk
from app.storage.keys import page_image_key, page_images_prefix

PARSER_VERSION = "page-image:1"
IMAGE_TYPES = frozenset({"png", "jpg", "jpeg", "tif", "tiff", "bmp", "webp"})
TEXT_TYPES = frozenset({"txt", "md", "markdown"})


class PageImageParser:
    def __init__(
        self,
        settings: ParsingSettings,
        *,
        ingest: str,
        render_dpi: int,
        converter_factory: Callable[
            [ParsingSettings], ConverterProvider
        ] = create_document_converter,
    ) -> None:
        self.settings = settings
        self.ingest = ingest
        self.render_dpi = render_dpi
        self.converter_factory = converter_factory

    def accepts(self, source: SourceFile) -> bool:
        return True

    async def parse(self, run: IngestRun, source: SourceFile) -> ParsedUnits:
        with tempfile.TemporaryDirectory(prefix="finoracle-images-") as directory:
            workdir = Path(directory)
            run.report("downloading")
            pages = await self._to_pages(run, source, workdir)
            if not pages:
                raise document_empty()
            # 重建时页数可能变少，先清掉旧图片
            await run.run_owned(
                run.storage.delete_prefix,
                page_images_prefix(source.document_id, source.version_id, self.ingest),
            )
            total = len(pages)
            for number, (path, _, _) in enumerate(pages, start=1):
                await run.run_owned(
                    run.storage.put_file,
                    page_image_key(
                        source.document_id, source.version_id, self.ingest, number
                    ),
                    path,
                    content_type="image/png",
                )
                run.report("saving", number, total)
        chunks = [
            BlockChunk(
                ordinal=number,
                content="",
                page_start=number,
                page_end=number,
                sources=[],
                bbox=[0.0, 0.0, width, height],
                modality="page",
            )
            for number, (_, width, height) in enumerate(pages, start=1)
        ]
        return ParsedUnits(
            chunks=list(chunks),
            status="completed",
            page_count=total,
            stats={"pages": total, "render_dpi": self.render_dpi},
            parser_version=PARSER_VERSION,
        )

    async def _to_pages(
        self, run: IngestRun, source: SourceFile, workdir: Path
    ) -> list[Page]:
        key = source.pdf_key or source.object_key
        extension = "pdf" if source.pdf_key else source.file_type
        original = workdir / f"source.{extension}"
        await asyncio.to_thread(download, run.storage, key, original)
        run.report("converting")
        return await asyncio.to_thread(self._convert, original, extension, workdir)

    def _convert(self, path: Path, extension: str, workdir: Path) -> list[Page]:
        if extension in IMAGE_TYPES:
            return normalize_image(path, workdir)
        if extension == "pdf":
            return render_pdf(path, workdir, self.render_dpi)
        if extension in TEXT_TYPES:
            # 原文可能是 GB18030；转换服务按带 BOM 的 UTF-8 文本读取
            text = decode_text(path.read_bytes())
            path = workdir / "source_utf8.txt"
            path.write_text(text, encoding="utf-8-sig")
        pdf = self.converter_factory(self.settings).to_pdf(path, workdir)
        return render_pdf(pdf, workdir, self.render_dpi)
