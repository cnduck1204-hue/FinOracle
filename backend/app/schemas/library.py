"""面向登录用户的制度库浏览接口响应结构。"""

from datetime import date
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.ingestion import UtcDateTime, ValidityStatus


class FacetCount(BaseModel):
    name: str
    count: int


class LibraryFacetsResponse(BaseModel):
    total_documents: int
    total_chunks: int = 0
    categories: list[FacetCount]
    tags: list[FacetCount]
    validity_counts: dict[str, int]


class LibraryPublishedVersion(BaseModel):
    id: UUID
    document_number: str | None
    version_note: str | None
    published_date: date | None
    effective_date: date | None
    validity_status: ValidityStatus
    validity_checked_at: date | None
    source_url: str | None
    page_count: int | None
    chunk_count: int
    published_at: UtcDateTime | None


class LibraryDocumentSummary(BaseModel):
    id: UUID
    title: str
    issuer: str | None
    category: str | None
    tags: list[str] = Field(default_factory=list)
    version: LibraryPublishedVersion
    updated_at: UtcDateTime


class LibraryDocumentPage(BaseModel):
    items: list[LibraryDocumentSummary]
    total: int
    page: int
    page_size: int
