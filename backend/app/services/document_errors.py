"""制度文档导入与管理的业务错误，错误码集中在此。解析本身的错误见 app.rag.parsing.errors。"""

from app.core.exceptions import AppException


def document_not_found() -> AppException:
    return AppException(
        code="DOCUMENT_NOT_FOUND", message="制度不存在", status_code=404
    )


def version_not_found() -> AppException:
    return AppException(
        code="VERSION_NOT_FOUND", message="文档版本不存在", status_code=404
    )


def job_not_found() -> AppException:
    return AppException(
        code="IMPORT_JOB_NOT_FOUND", message="导入任务不存在", status_code=404
    )


def duplicate_version() -> AppException:
    return AppException(
        code="DOCUMENT_VERSION_DUPLICATE",
        message="该制度已有相同文件的版本",
        status_code=409,
    )


def version_state_conflict(message: str) -> AppException:
    return AppException(code="VERSION_STATE_CONFLICT", message=message, status_code=409)


def import_in_progress() -> AppException:
    return AppException(
        code="IMPORT_IN_PROGRESS",
        message="该版本正在处理，请等待当前任务结束",
        status_code=409,
    )


def job_not_retryable() -> AppException:
    return AppException(
        code="IMPORT_JOB_NOT_RETRYABLE", message="只能重试失败的任务", status_code=409
    )


def file_not_available() -> AppException:
    return AppException(
        code="FILE_NOT_AVAILABLE", message="该版本没有可预览的 PDF", status_code=404
    )


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
