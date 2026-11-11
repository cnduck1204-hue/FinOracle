"""PDF 文本块解析器：用 PyMuPDF 取文本块和表格，每个单元保留页码与坐标。

不做 OCR，也不识别条文结构，扫描页没有单元。相邻文本块合并到上限字数，字号明显大于
本页中位数的短行作为标题；表格单独成单元。坐标为 PDF 点，原点在页面左上角。
"""

import asyncio
import re
import tempfile
from pathlib import Path

import pymupdf

from app.rag.files import download
from app.rag.models import IngestRun, ParsedUnits, SourceFile
from app.rag.parsing.errors import document_empty, source_unsupported
from app.rag.parsing.models import Chunk

PARSER_VERSION = "pdf-block:1"
MAX_UNIT_CHARS = 1200
# 合并后仍短于此长度的片段很少是有用证据
_MIN_UNIT_CHARS = 12
_HEADING_SIZE_RATIO = 1.25
_MAX_HEADING_CHARS = 120
_TABLE_OVERLAP_RATIO = 0.35
_WHITESPACE = re.compile(r"\s+")


class BlockChunk(Chunk):
    bbox: list[float]
    modality: str = "text"


def _normalize(text: str) -> str:
    return _WHITESPACE.sub(" ", text).strip()


def _block_text(block: dict) -> tuple[str, float]:
    """拼接文本块的各行，返回文本与最大字号。"""
    lines: list[str] = []
    max_size = 0.0
    for line in block.get("lines", []):
        parts: list[str] = []
        for span in line.get("spans", []):
            parts.append(span.get("text", ""))
            max_size = max(max_size, float(span.get("size") or 0.0))
        joined = "".join(parts)
        if joined.strip():
            lines.append(joined)
    return _normalize("\n".join(lines)), max_size


def _median_size(blocks: list[dict]) -> float:
    sizes = sorted(
        float(span.get("size") or 0.0)
        for block in blocks
        for line in block.get("lines", [])
        for span in line.get("spans", [])
        if span.get("text", "").strip()
    )
    return sizes[len(sizes) // 2] if sizes else 0.0


def _overlaps_any(box: pymupdf.Rect, rects: list[pymupdf.Rect]) -> bool:
    for rect in rects:
        intersection = box & rect
        if (
            not intersection.is_empty
            and intersection.get_area() > _TABLE_OVERLAP_RATIO * box.get_area()
        ):
            return True
    return False


def _find_tables(page: pymupdf.Page) -> list:
    try:
        return list(page.find_tables().tables)
    except Exception:  # noqa: BLE001 - 版面分析失败时按没有表格处理
        return []


def _table_text(table) -> str:
    try:
        rows = table.extract()
    except Exception:  # noqa: BLE001
        return ""
    lines = []
    for row in rows or []:
        cells = [_normalize(str(cell or "")) for cell in row]
        if any(cells):
            lines.append(" | ".join(cells))
    return "\n".join(lines)


class _PageUnits:
    """收集一页的单元，统一编号。"""

    def __init__(self, chunks: list[BlockChunk], page: int) -> None:
        self.chunks = chunks
        self.page = page

    def add(self, text: str, bbox: list[float], modality: str, heading: str) -> None:
        self.chunks.append(
            BlockChunk(
                ordinal=len(self.chunks) + 1,
                heading=heading,
                content=text,
                page_start=self.page,
                page_end=self.page,
                sources=["text"],
                bbox=bbox,
                modality=modality,
            )
        )


def _parse_page(page: pymupdf.Page, units: _PageUnits) -> None:
    tables = _find_tables(page)
    table_rects = [pymupdf.Rect(table.bbox) for table in tables]
    for table, rect in zip(tables, table_rects, strict=True):
        text = _table_text(table)
        if text:
            units.add(text, [rect.x0, rect.y0, rect.x1, rect.y1], "table", "")

    blocks = [
        block
        for block in page.get_text("dict").get("blocks", [])
        if block.get("type") == 0
    ]
    median_size = _median_size(blocks)
    pending: list[str] = []
    box: list[float] | None = None
    heading = ""

    def flush() -> None:
        nonlocal pending, box
        merged = _normalize(" ".join(pending))
        if box is not None and len(merged) >= _MIN_UNIT_CHARS:
            units.add(merged, box, "text", heading)
        pending, box = [], None

    for block in sorted(
        blocks, key=lambda item: (round(item["bbox"][1], 1), item["bbox"][0])
    ):
        bbox = [float(value) for value in block["bbox"]]
        if _overlaps_any(pymupdf.Rect(bbox), table_rects):
            continue
        text, max_size = _block_text(block)
        if not text:
            continue
        is_heading = (
            median_size > 0
            and max_size >= median_size * _HEADING_SIZE_RATIO
            and len(text) <= _MAX_HEADING_CHARS
            and not text.endswith((".", "。", "!", "?"))
        )
        if is_heading:
            flush()
            heading = text
            continue
        if box is None:
            box = list(bbox)
        else:
            box = [
                min(box[0], bbox[0]),
                min(box[1], bbox[1]),
                max(box[2], bbox[2]),
                max(box[3], bbox[3]),
            ]
        pending.append(text)
        if sum(map(len, pending)) >= MAX_UNIT_CHARS:
            flush()
    flush()


def parse_pdf_blocks(path: Path) -> tuple[list[BlockChunk], int]:
    chunks: list[BlockChunk] = []
    with pymupdf.open(path) as document:
        for index in range(document.page_count):
            _parse_page(document.load_page(index), _PageUnits(chunks, index + 1))
        return chunks, document.page_count


class PdfBlockParser:
    def accepts(self, source: SourceFile) -> bool:
        return source.pdf_key is not None

    async def parse(self, run: IngestRun, source: SourceFile) -> ParsedUnits:
        key = source.pdf_key
        if key is None:
            raise source_unsupported()
        with tempfile.TemporaryDirectory(prefix="finoracle-blocks-") as directory:
            path = Path(directory) / "source.pdf"
            run.report("downloading")
            await asyncio.to_thread(download, run.storage, key, path)
            run.report("parsing")
            chunks, page_count = await asyncio.to_thread(parse_pdf_blocks, path)
        if not chunks:
            raise document_empty()
        return ParsedUnits(
            chunks=list(chunks),
            status="completed",
            page_count=page_count,
            stats={
                "units": len(chunks),
                "tables": sum(chunk.modality == "table" for chunk in chunks),
            },
            parser_version=PARSER_VERSION,
        )
