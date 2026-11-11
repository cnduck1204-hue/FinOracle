"""向量化与向量库的错误。5xx 由 worker 按次数重试，4xx 直接失败。"""

from app.core.exceptions import AppException


def embedding_unavailable(message: str = "向量化服务暂时不可用") -> AppException:
    return AppException(code="EMBEDDING_UNAVAILABLE", message=message, status_code=503)


def embedding_auth_failed() -> AppException:
    """密钥无效或无权限，重试不会成功；更正配置并重启 worker 后用重试接口重新执行。"""
    return AppException(
        code="EMBEDDING_AUTH_FAILED",
        message="向量化服务拒绝了密钥，请检查 DASHSCOPE_API_KEY",
        status_code=424,
    )


def embedding_rejected(message: str) -> AppException:
    """请求本身不被接受（例如文本过长），重试也不会成功。"""
    return AppException(code="EMBEDDING_REJECTED", message=message, status_code=422)


def vector_store_unavailable() -> AppException:
    return AppException(
        code="VECTOR_STORE_UNAVAILABLE",
        message="向量库暂时不可用，请稍后重试",
        status_code=503,
    )


def visual_unavailable(message: str = "视觉编码服务暂时不可用") -> AppException:
    return AppException(code="VISUAL_UNAVAILABLE", message=message, status_code=503)
