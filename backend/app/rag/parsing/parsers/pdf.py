"""PDF：直接提取文本层与表格，扫描页和大图区域交给 OCR。"""

from pathlib import Path
from typing import Literal

import pdfplumber
from pdfplumber.page import Page
from pdfplumber.table import Table
from pdfplumber.utils.exceptions import MalformedPDFException, PdfminerException

from app.rag.parsing.errors import invalid_document, too_many_pages
from app.rag.parsing.models import OcrReason, ParsedDocument
from app.rag.parsing.options import ParseOptions
from app.rag.parsing.parsers.base import ParserProvider, image_to_png, table_text

# PDF 坐标单位为点，1 英寸 = 72 点
_POINTS_PER_INCH = 72

Box = tuple[float, float, float, float]
# 页面内容按纵向位置排序：文本行、表格、需要 OCR 的图片区域
LayoutItem = tuple[Literal["line", "table", "image"], str | Table | Box]


class PdfParser(ParserProvider):
    file_types = frozenset({"pdf"})

    def parse(self, path: Path, options: ParseOptions) -> ParsedDocument:
        try:
            with pdfplumber.open(path) as pdf:
                if len(pdf.pages) > options.max_pages:
                    raise too_many_pages(options.max_pages)
                document = ParsedDocument(page_count=len(pdf.pages))
                for page in pdf.pages:
                    _parse_page(page, document, options)
        except (PdfminerException, MalformedPDFException) as exc:
            raise invalid_document("PDF 文件损坏、加密或无法读取") from exc
        return document


def _parse_page(page: Page, document: ParsedDocument, options: ParseOptions) -> None:
    chars = [char for char in page.chars if not char["text"].isspace()]
    # 文本层不足且有图片或矢量图形时按扫描页整页识别；空白页、短文本页直接读文本层
    if len(chars) < options.min_text_layer_chars and (page.images or page.curves):
        _add_ocr_target(document, page, page.bbox, "scanned_page", options)
        return

    number = page.page_number
    lines: list[str] = []
    for kind, value in _layout(page, chars, options):
        if kind == "line":
            lines.append(value)
            continue
        # 表格和图片打断段落，先输出已收集的文本行
        if lines:
            document.add_text("\n".join(lines), page=number)
            lines.clear()
        if kind == "table":
            text = table_text(value.extract())
            if text:
                document.add_text(text, page=number, kind="table")
        else:
            _add_ocr_target(document, page, value, "image_region", options)
    if lines:
        document.add_text("\n".join(lines), page=number)


def _layout(page: Page, chars: list[dict], options: ParseOptions) -> list[LayoutItem]:
    tables = page.find_tables()
    table_boxes = [table.bbox for table in tables]
    # 表格内的文字由表格输出，不再作为文本行重复出现
    content = (
        page.filter(lambda obj: not _inside(obj, table_boxes)) if table_boxes else page
    )

    items: list[tuple[float, LayoutItem]] = [
        (line["top"], ("line", line["text"]))
        for line in content.extract_text_lines(return_chars=False)
    ]
    items.extend((table.bbox[1], ("table", table)) for table in tables)
    items.extend((box[1], ("image", box)) for box in _ocr_regions(page, chars, options))
    items.sort(key=lambda item: item[0])
    return [item for _, item in items]


def _ocr_regions(page: Page, chars: list[dict], options: ParseOptions) -> list[Box]:
    """面积足够大且没有文本层覆盖的图片区域。"""
    page_area = float(page.width * page.height)
    regions = []
    for image in page.images:
        box = _clip(
            (image["x0"], image["top"], image["x1"], image["bottom"]), page.bbox
        )
        if box is None:
            continue
        area = (box[2] - box[0]) * (box[3] - box[1])
        if area / page_area < options.min_image_area_ratio:
            continue
        # 可检索的扫描件、带文字叠层的插图已有文本层，不再重复识别
        overlaid = sum(1 for char in chars if _inside(char, [box]))
        if overlaid < options.min_text_layer_chars:
            regions.append(box)
    return regions


def _add_ocr_target(
    document: ParsedDocument,
    page: Page,
    box: Box,
    reason: OcrReason,
    options: ParseOptions,
) -> None:
    region = page if box == page.bbox else page.crop(box)
    # 按最长边限制渲染尺寸，避免超大页面占用过多内存
    longest = max(region.width, region.height)
    max_dpi = _POINTS_PER_INCH * options.max_render_side / longest
    rendered = region.to_image(resolution=min(options.render_dpi, max_dpi)).original
    image = image_to_png(rendered, options.min_image_pixels)
    if image is not None:
        document.add_image(image, reason=reason, page=page.page_number)


def _inside(obj: dict, boxes: list[Box]) -> bool:
    """对象中心点是否落在任一区域内。"""
    x = (obj["x0"] + obj["x1"]) / 2
    y = (obj["top"] + obj["bottom"]) / 2
    return any(x0 <= x <= x1 and top <= y <= bottom for x0, top, x1, bottom in boxes)


def _clip(box: Box, bounds: Box) -> Box | None:
    """裁到页面范围内；裁剪后不足 1 点宽或高时返回 None。"""
    x0, top = max(box[0], bounds[0]), max(box[1], bounds[1])
    x1, bottom = min(box[2], bounds[2]), min(box[3], bounds[3])
    if x1 - x0 < 1 or bottom - top < 1:
        return None
    return (x0, top, x1, bottom)
