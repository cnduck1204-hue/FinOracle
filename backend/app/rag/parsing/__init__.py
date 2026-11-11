"""文档解析：按需格式转换 → 解析 → 按需 OCR → 清洗 → 切片，入口为 parse_file。

各阶段的可替换实现分别位于 converters/、parsers/、ocr/、chunkers/，扩展方式见各目录的
__init__.py。
"""

from app.rag.parsing.chunkers import CHUNKER_CHOICES
from app.rag.parsing.models import Chunk, ParseResult, TextBlock
from app.rag.parsing.ocr import OCR_CHOICES, create_ocr
from app.rag.parsing.options import AUTO, PARSER_VERSION, ParseOptions
from app.rag.parsing.pipeline import (
    SUPPORTED_TYPES,
    ProgressCallback,
    convert_to_pdf,
    parse_file,
)

__all__ = [
    "AUTO",
    "CHUNKER_CHOICES",
    "OCR_CHOICES",
    "PARSER_VERSION",
    "SUPPORTED_TYPES",
    "Chunk",
    "ParseOptions",
    "ParseResult",
    "ProgressCallback",
    "TextBlock",
    "convert_to_pdf",
    "create_ocr",
    "parse_file",
]
