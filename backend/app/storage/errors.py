"""对象存储的业务错误。"""

from app.core.exceptions import AppException


def storage_unavailable() -> AppException:
    return AppException(
        code="STORAGE_UNAVAILABLE",
        message="文件存储暂时不可用，请稍后重试",
        status_code=503,
    )
