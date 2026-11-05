"""文档解析器：按扩展名选择；.doc 等需先转换的格式见 converters/。

新增格式：在本目录添加模块，继承 ParserProvider 并实现 parse，再加入 PARSERS。
"""

from app.rag.parsing.errors import unsupported_type
from app.rag.parsing.parsers.base import ParserProvider
from app.rag.parsing.parsers.image import ImageParser
from app.rag.parsing.parsers.pdf import PdfParser
from app.rag.parsing.parsers.text import TextParser
from app.rag.parsing.parsers.word import WordParser

PARSERS: tuple[ParserProvider, ...] = (
    PdfParser(),
    WordParser(),
    TextParser(),
    ImageParser(),
)
PARSER_TYPES = frozenset().union(*(parser.file_types for parser in PARSERS))


def get_parser(file_type: str) -> ParserProvider:
    for parser in PARSERS:
        if file_type in parser.file_types:
            return parser
    raise unsupported_type(file_type)


__all__ = ["PARSERS", "PARSER_TYPES", "ParserProvider", "get_parser"]
