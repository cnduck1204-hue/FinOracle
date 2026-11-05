"""格式转换器：把 .doc 等旧格式转换为 PDF 后再解析，两种实现输出一致。

配置了 Gotenberg 地址时使用 GotenbergConverter，否则使用本机 LibreOffice。
新增可转换格式：确认 LibreOffice 支持后，在 _SIGNATURES 中登记扩展名及其文件头。
"""

from pathlib import Path

from app.rag.parsing.converters.base import ConverterProvider
from app.rag.parsing.converters.gotenberg import GotenbergConverter
from app.rag.parsing.converters.libreoffice import LibreOfficeConverter
from app.rag.parsing.errors import invalid_document

_OLE2 = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_RTF = b"{\\rtf"
_ZIP = b"PK\x03\x04"
# 网站下载的 .doc 常是 HTML、MHT 或 Word 2003 XML，LibreOffice 能正确转换
_MARKUP = (b"<html", b"<!doctype html", b"<?xml", b"mime-version:")
# LibreOffice 会把无法识别的内容当作纯文本转换，产生乱码，因此转换前先核对文件头
_SIGNATURES: dict[str, tuple[bytes, ...]] = {
    "doc": (_OLE2, _RTF, _ZIP, *_MARKUP),
    "rtf": (_RTF,),
    "odt": (_ZIP,),
}
_HEADER_BYTES = 512

# 先转换为 PDF 再解析的格式；.docx 由 Word 解析器直接读取，保留标题与表格结构
CONVERTIBLE_TYPES = frozenset(_SIGNATURES)


def check_signature(path: Path, file_type: str) -> None:
    with path.open("rb") as file:
        header = file.read(_HEADER_BYTES).lstrip(b"\xef\xbb\xbf \t\r\n").lower()
    if not header.startswith(tuple(sign.lower() for sign in _SIGNATURES[file_type])):
        raise invalid_document(f"文件内容不是有效的 .{file_type} 文档")


def create_converter(
    *, gotenberg_url: str | None, soffice_path: str | None, timeout_seconds: float
) -> ConverterProvider:
    if gotenberg_url:
        return GotenbergConverter(gotenberg_url, timeout_seconds)
    return LibreOfficeConverter(soffice_path, timeout_seconds)


__all__ = [
    "CONVERTIBLE_TYPES",
    "ConverterProvider",
    "GotenbergConverter",
    "LibreOfficeConverter",
    "check_signature",
    "create_converter",
]
