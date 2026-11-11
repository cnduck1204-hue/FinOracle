"""Pipeline 相关的业务错误。"""

from app.core.exceptions import AppException


def pipeline_not_found() -> AppException:
    return AppException(
        code="PIPELINE_NOT_FOUND", message="检索方案不存在", status_code=404
    )


def pipeline_unavailable(message: str) -> AppException:
    return AppException(code="PIPELINE_UNAVAILABLE", message=message, status_code=409)


def source_unsupported() -> AppException:
    return AppException(
        code="PIPELINE_SOURCE_UNSUPPORTED",
        message="该方案只能处理 PDF，或已转换为 PDF 的文档",
        status_code=422,
    )


def visual_unavailable(message: str = "视觉编码服务暂时不可用") -> AppException:
    return AppException(code="VISUAL_UNAVAILABLE", message=message, status_code=503)
