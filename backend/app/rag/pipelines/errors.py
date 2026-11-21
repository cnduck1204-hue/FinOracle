"""Pipeline 相关的业务错误。"""

from app.core.exceptions import AppException


def pipeline_not_found() -> AppException:
    return AppException(
        code="PIPELINE_NOT_FOUND", message="检索方案不存在", status_code=404
    )


def pipeline_unavailable(message: str) -> AppException:
    return AppException(code="PIPELINE_UNAVAILABLE", message=message, status_code=409)
