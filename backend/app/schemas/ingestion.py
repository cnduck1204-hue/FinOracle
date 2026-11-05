"""制度文档导入与管理接口的请求、响应模型。"""

from datetime import UTC, date, datetime
from typing import Annotated, Any, Literal, Self
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import UploadFile
from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from app.models.document import PARSE_STATUSES, VALIDITY_STATUSES, VERSION_STATUSES
from app.models.import_job import JOB_KINDS, JOB_STATUSES
from app.schemas.document import ParseOptionsInput


def _as_utc(value: datetime) -> datetime:
    """数据库按 UTC 存储不带时区的时间，输出时补回时区。"""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _http_url(value: str) -> str:
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.netloc or " " in value:
        raise ValueError("请提供 http 或 https 网址")
    return value


UtcDateTime = Annotated[datetime, AfterValidator(_as_utc)]
HttpUrlText = Annotated[str, Field(max_length=2048), AfterValidator(_http_url)]
ShortText = Annotated[str, Field(min_length=1, max_length=255)]
VersionStatus = Literal[VERSION_STATUSES]
ValidityStatus = Literal[VALIDITY_STATUSES]
ParseStatus = Literal[PARSE_STATUSES]
JobKind = Literal[JOB_KINDS]
JobStatus = Literal[JOB_STATUSES]

# 版本元数据中可以为空的字段；表单留空提交的空字符串按未填写处理
_OPTIONAL_METADATA = (
    "document_number",
    "version_note",
    "published_date",
    "effective_date",
    "validity_checked_at",
    "source_url",
    "download_url",
    "expected_articles",
)


class VersionMetadata(BaseModel):
    """版本的登记信息：文号、版本说明、日期、时效与来源。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    document_number: ShortText | None = Field(default=None, description="文号")
    version_note: ShortText | None = Field(
        default=None, description="版本说明，如“2023年第二次修订”"
    )
    published_date: date | None = None
    effective_date: date | None = None
    validity_status: ValidityStatus = Field(
        default="unknown", description="时效状态；未核实时为 unknown"
    )
    validity_checked_at: date | None = Field(default=None, description="时效核查日期")
    source_url: HttpUrlText | None = Field(default=None, description="发布依据页面")
    download_url: HttpUrlText | None = None
    expected_articles: int | None = Field(
        default=None, ge=1, le=100_000, description="应有条数，用于校对切片"
    )

    @field_validator(*_OPTIONAL_METADATA, mode="before")
    @classmethod
    def blank_as_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value


class ImportForm(VersionMetadata, ParseOptionsInput):
    """导入接口的 multipart 表单：文件、所属制度或新制度信息、版本元数据与解析参数。"""

    # format=binary 兼容只识别 OpenAPI 3.0 写法的工具，导入后显示为文件字段
    file: UploadFile = Field(
        description="PDF、Word（.docx/.doc）、RTF、ODT、TXT/Markdown 或图片，最大 50 MB",
        json_schema_extra={"format": "binary"},
    )
    document_id: UUID | None = Field(
        default=None, description="为已有制度上传新版本；不填则新建制度"
    )
    title: ShortText | None = Field(default=None, description="新建制度时必填")
    issuer: ShortText | None = Field(default=None, description="发文机构")

    @field_validator("document_id", "title", "issuer", mode="before")
    @classmethod
    def blank_identity_as_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @model_validator(mode="after")
    def check_document(self) -> Self:
        if self.document_id is None and self.title is None:
            raise ValueError("新建制度时必须提供标题")
        if self.document_id is not None and (self.title or self.issuer):
            raise ValueError("为已有制度上传版本时不能修改制度信息")
        return self

    def metadata(self) -> dict[str, Any]:
        return self.model_dump(include=set(VersionMetadata.model_fields))


class DocumentUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: ShortText | None = None
    issuer: ShortText | None = None

    @field_validator("title", mode="before")
    @classmethod
    def reject_null_title(cls, value: object) -> object:
        if value is None:
            raise ValueError("标题不能为空")
        return value

    @model_validator(mode="after")
    def require_update(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("请至少提供一个字段")
        return self


class VersionUpdate(BaseModel):
    """修改版本登记信息；可为空的字段传 null 表示清除。只改元数据，不影响切片。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    document_number: ShortText | None = None
    version_note: ShortText | None = None
    published_date: date | None = None
    effective_date: date | None = None
    validity_status: ValidityStatus | None = None
    validity_checked_at: date | None = None
    source_url: HttpUrlText | None = None
    download_url: HttpUrlText | None = None
    expected_articles: int | None = Field(default=None, ge=1, le=100_000)

    @field_validator("validity_status", mode="before")
    @classmethod
    def reject_null_validity(cls, value: object) -> object:
        if value is None:
            raise ValueError("时效状态不能为 null")
        return value

    @model_validator(mode="after")
    def require_update(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("请至少提供一个字段")
        return self


class VersionBrief(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    status: VersionStatus
    version_note: str | None
    validity_status: ValidityStatus
    published_at: UtcDateTime | None
    created_at: UtcDateTime


class DocumentResponse(BaseModel):
    id: UUID
    external_id: str | None
    title: str
    issuer: str | None
    version_count: int
    # 当前发布版本；没有时为 null
    published_version: VersionBrief | None
    # 最近上传的版本，用于展示处理进度
    latest_version: VersionBrief | None
    created_by: UUID | None
    created_at: UtcDateTime
    updated_at: UtcDateTime


class DocumentPage(BaseModel):
    items: list[DocumentResponse]
    total: int
    page: int
    page_size: int


class JobResponse(BaseModel):
    id: UUID
    version_id: UUID
    document_id: UUID
    document_title: str
    file_name: str
    kind: JobKind
    status: JobStatus
    # downloading / converting / parsing / ocr / chunking / saving；未开始或已结束时为 null
    stage: str | None
    progress_current: int
    progress_total: int | None
    attempts: int
    max_attempts: int
    run_after: UtcDateTime
    error_code: str | None
    error_message: str | None
    created_by: UUID | None
    created_at: UtcDateTime
    started_at: UtcDateTime | None
    finished_at: UtcDateTime | None
    updated_at: UtcDateTime


class JobPage(BaseModel):
    items: list[JobResponse]
    total: int
    page: int
    page_size: int


class VersionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    document_id: UUID
    status: VersionStatus
    document_number: str | None
    version_note: str | None
    published_date: date | None
    effective_date: date | None
    validity_status: ValidityStatus
    validity_checked_at: date | None
    source_url: str | None
    download_url: str | None
    file_name: str
    file_type: str
    size_bytes: int
    sha256: str
    # 可用 variant=pdf 预览 PDF：原件即 PDF，或已由 .doc 等转换
    has_pdf: bool
    parse_options: dict[str, Any]
    parser_version: str | None
    parse_status: ParseStatus | None
    page_count: int | None
    chunk_count: int
    expected_articles: int | None
    parse_stats: dict[str, Any] | None
    parse_warnings: list[str] | None
    index_model: str | None
    indexed_at: UtcDateTime | None
    # 最近一次任务，用于展示处理阶段与失败原因
    latest_job: JobResponse | None
    created_by: UUID | None
    published_by: UUID | None
    published_at: UtcDateTime | None
    created_at: UtcDateTime
    updated_at: UtcDateTime


class ChunkResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    ordinal: int
    external_id: str | None
    chapter: str
    section: str
    article: str
    heading: str
    part: int
    part_count: int
    content: str
    page_start: int | None
    page_end: int | None
    sources: list[str]


class ChunkPage(BaseModel):
    items: list[ChunkResponse]
    total: int
    page: int
    page_size: int
