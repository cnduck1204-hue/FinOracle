"""格式转换器基类：把解析器不能直接读取的格式转换为 PDF。"""

from abc import ABC, abstractmethod
from pathlib import Path

from app.rag.parsing.errors import invalid_document

_PDF_SIGNATURE = b"%PDF-"


class ConverterProvider(ABC):
    name: str

    @abstractmethod
    def to_pdf(self, path: Path, workdir: Path) -> Path:
        """把 path 转换为 PDF 写入 workdir，返回 PDF 路径。"""


def checked_pdf(path: Path) -> Path:
    """确认转换结果存在且是 PDF。"""
    if path.is_file():
        with path.open("rb") as file:
            if file.read(len(_PDF_SIGNATURE)) == _PDF_SIGNATURE:
                return path
    raise invalid_document("文档无法转换为 PDF")
