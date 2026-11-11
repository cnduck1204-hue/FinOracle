"""PDF 页面渲染与图片规范化。"""

from pathlib import Path

import pymupdf
from PIL import Image, ImageSequence, UnidentifiedImageError

from app.rag.parsing.errors import invalid_document

Page = tuple[Path, float, float]


def render_pages(path: Path, start: int, stop: int, dpi: int) -> list[bytes]:
    """把第 start 到 stop - 1 页（从 0 起）渲染为 PNG。"""
    with pymupdf.open(path) as document:
        return [
            document.load_page(index).get_pixmap(dpi=dpi, alpha=False).tobytes("png")
            for index in range(start, min(stop, document.page_count))
        ]


def page_count(path: Path) -> int:
    with pymupdf.open(path) as document:
        return document.page_count


def render_pdf(path: Path, directory: Path, dpi: int) -> list[Page]:
    """把 PDF 每页渲染为 PNG，返回 (图片路径, 页宽, 页高)。"""
    pages: list[Page] = []
    try:
        document = pymupdf.open(path)
    except (pymupdf.FileDataError, RuntimeError) as error:
        raise invalid_document("文件内容不是有效的 PDF") from error
    with document:
        if document.needs_pass:
            raise invalid_document("文档已加密，无法读取")
        for index in range(document.page_count):
            page = document.load_page(index)
            target = directory / f"{index + 1}.png"
            page.get_pixmap(dpi=dpi, alpha=False).save(target)
            pages.append((target, page.rect.width, page.rect.height))
    return pages


def normalize_image(path: Path, directory: Path) -> list[Page]:
    """把图片文件的每一帧（多页 TIFF）保存为 PNG。"""
    pages: list[Page] = []
    try:
        with Image.open(path) as image:
            for index, frame in enumerate(ImageSequence.Iterator(image), start=1):
                target = directory / f"{index}.png"
                converted = frame.convert("RGB")
                converted.save(target, format="PNG")
                pages.append((target, float(converted.width), float(converted.height)))
    except (UnidentifiedImageError, OSError) as error:
        raise invalid_document("文件内容不是有效的图片") from error
    return pages
