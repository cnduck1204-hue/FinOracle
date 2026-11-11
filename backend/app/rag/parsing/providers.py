"""按部署配置创建格式转换器，并在进程内复用 OCR 引擎。"""

from functools import lru_cache

from app.core.config import ParsingSettings
from app.rag.parsing.converters import ConverterProvider, create_converter
from app.rag.parsing.ocr import OCRProvider, create_ocr


def create_document_converter(settings: ParsingSettings) -> ConverterProvider:
    """配置了 Gotenberg 时使用它，否则使用本机 LibreOffice。"""
    return create_converter(
        gotenberg_url=settings.gotenberg_url,
        soffice_path=settings.soffice_path,
        timeout_seconds=settings.conversion_timeout_seconds,
    )


@lru_cache
def ocr_provider(name: str) -> OCRProvider | None:
    """OCR 引擎加载模型需要数秒，按名称缓存后在进程内复用。"""
    return create_ocr(name)
