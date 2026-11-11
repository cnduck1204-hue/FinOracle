"""Word（.docx）：按正文顺序读取标题、段落、表格和内嵌图片。"""

import re
from pathlib import Path
from zipfile import BadZipFile

from docx import Document
from docx.document import Document as DocxDocument
from docx.opc.exceptions import PackageNotFoundError
from docx.table import Table
from docx.text.paragraph import Paragraph

from app.rag.parsing.errors import invalid_document
from app.rag.parsing.models import ParsedDocument
from app.rag.parsing.options import ParseOptions
from app.rag.parsing.parsers.base import ParserProvider, image_to_png, table_text

# Word 内置标题样式名，英文与中文界面各一种写法
_HEADING_STYLE = re.compile(r"^(?:heading|标题)\s*(\d)$", re.IGNORECASE)
_TITLE_STYLES = {"title", "标题"}


class WordParser(ParserProvider):
    file_types = frozenset({"docx"})

    def parse(self, path: Path, options: ParseOptions) -> ParsedDocument:
        try:
            document = Document(str(path))
        except (PackageNotFoundError, BadZipFile, KeyError, ValueError) as exc:
            raise invalid_document("Word 文件损坏或无法读取") from exc

        parsed = ParsedDocument(page_count=None)
        skipped_images = 0
        for item in document.iter_inner_content():
            if isinstance(item, Table):
                text = table_text(_table_rows(item))
                if text:
                    parsed.add_text(text, kind="table")
                continue

            level = _heading_level(item)
            if item.text.strip():
                kind = "paragraph" if level is None else "heading"
                parsed.add_text(item.text, kind=kind, level=level)
            for image in _paragraph_images(document, item, options.min_image_pixels):
                if image is None:
                    skipped_images += 1
                else:
                    parsed.add_image(image, reason="embedded_image")

        if skipped_images:
            parsed.warnings.append(
                f"跳过 {skipped_images} 张过小或格式不支持的内嵌图片"
            )
        return parsed


def _paragraph_images(
    document: DocxDocument, paragraph: Paragraph, min_pixels: int
) -> list[bytes | None]:
    """段落内嵌图片转为 PNG；过小或格式不支持的为 None。"""
    images = []
    for rid in paragraph._p.xpath(".//a:blip/@r:embed"):
        part = document.part.related_parts.get(rid)
        images.append(image_to_png(part.blob, min_pixels) if part else None)
    return images


def _heading_level(paragraph: Paragraph) -> int | None:
    name = (paragraph.style.name if paragraph.style is not None else "").strip()
    if name.lower() in _TITLE_STYLES:
        return 0
    match = _HEADING_STYLE.match(name)
    return int(match.group(1)) if match else None


def _table_rows(table: Table) -> list[list[str]]:
    rows = []
    for row in table.rows:
        seen = set()
        cells = []
        # 合并单元格在 python-docx 中会重复返回同一个 tc
        for cell in row.cells:
            if id(cell._tc) in seen:
                continue
            seen.add(id(cell._tc))
            cells.append(cell.text)
        rows.append(cells)
    return rows
