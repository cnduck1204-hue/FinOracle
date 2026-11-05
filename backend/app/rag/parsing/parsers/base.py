"""Parser 基类与各格式共用的工具。"""

import io
from abc import ABC, abstractmethod
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from app.rag.parsing.models import ParsedDocument
from app.rag.parsing.options import ParseOptions


class ParserProvider(ABC):
    """把 file_types 中的格式解析为文本块；需要识别的图片交给 OCR 阶段。"""

    file_types: frozenset[str]

    @abstractmethod
    def parse(self, path: Path, options: ParseOptions) -> ParsedDocument: ...


def table_text(rows: list[list[str | None]]) -> str:
    """表格按行输出，单元格以 | 分隔，单元格内换行压成空格。"""
    lines = []
    for row in rows:
        cells = [" ".join((cell or "").split()) for cell in row]
        if any(cells):
            lines.append(" | ".join(cells))
    return "\n".join(lines)


def image_to_png(data: bytes | Image.Image, min_pixels: int = 0) -> bytes | None:
    """统一转为 OCR 使用的 PNG；无法识别或小于 min_pixels 的图片返回 None。"""
    try:
        image = data if isinstance(data, Image.Image) else Image.open(io.BytesIO(data))
        if image.width * image.height < min_pixels:
            return None
        if image.mode in ("RGBA", "LA", "P", "PA"):
            # 透明区域铺白底，否则转 RGB 后透明处变黑会盖住文字
            rgba = image.convert("RGBA")
            image = Image.new("RGB", rgba.size, "white")
            image.paste(rgba, mask=rgba.getchannel("A"))
        elif image.mode not in ("RGB", "L"):
            image = image.convert("RGB")
        output = io.BytesIO()
        image.save(output, format="PNG")
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        return None
    return output.getvalue()
