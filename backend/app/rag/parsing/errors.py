"""解析模块的全部业务错误，错误码集中在此。"""

from app.core.exceptions import AppException
from app.rag.parsing.options import MIB


def invalid_document(message: str) -> AppException:
    return AppException(code="DOCUMENT_INVALID", message=message, status_code=422)


def unsupported_type(file_type: str) -> AppException:
    return AppException(
        code="DOCUMENT_UNSUPPORTED_TYPE",
        message=f"不支持的文件类型：{file_type or '无扩展名'}",
        status_code=415,
    )


def file_too_large(limit_bytes: int) -> AppException:
    return AppException(
        code="DOCUMENT_TOO_LARGE",
        message=f"文件超过 {limit_bytes // MIB} MB 上限",
        status_code=413,
    )


def too_many_pages(limit: int) -> AppException:
    return AppException(
        code="DOCUMENT_TOO_MANY_PAGES",
        message=f"文档超过 {limit} 页上限",
        status_code=413,
    )


def conversion_unavailable(message: str) -> AppException:
    return AppException(
        code="DOCUMENT_CONVERSION_UNAVAILABLE", message=message, status_code=503
    )


def ocr_unavailable(message: str) -> AppException:
    return AppException(code="OCR_UNAVAILABLE", message=message, status_code=503)


def file_missing() -> AppException:
    return AppException(
        code="DOCUMENT_FILE_MISSING", message="文档原件不存在", status_code=422
    )


def document_empty() -> AppException:
    return AppException(
        code="DOCUMENT_EMPTY", message="文档中没有可用的文本", status_code=422
    )


def invalid_parse_options() -> AppException:
    return AppException(
        code="PARSE_OPTIONS_INVALID", message="解析参数不合法", status_code=422
    )


def source_unsupported() -> AppException:
    return AppException(
        code="PIPELINE_SOURCE_UNSUPPORTED",
        message="该方案只能处理 PDF，或已转换为 PDF 的文档",
        status_code=422,
    )
