"""检索接口的请求与响应。"""

from datetime import date
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.schemas.ingestion import ValidityStatus

QueryText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)
]


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: QueryText = Field(description="检索问题或关键词")
    document_ids: list[UUID] | None = Field(
        default=None,
        max_length=50,
        description="只在这些制度的已发布版本中检索；不填则检索全部已发布制度",
    )
    top_k: int = Field(default=8, ge=1, le=20, description="返回的条文数")
    pipeline: str | None = Field(
        default=None, max_length=64, description="检索方案；不填使用默认方案"
    )


class SearchDocument(BaseModel):
    id: UUID
    title: str
    issuer: str | None


class SearchVersion(BaseModel):
    id: UUID
    document_number: str | None
    version_note: str | None
    published_date: date | None
    effective_date: date | None
    # 未核实时为 unknown，不能显示为现行有效
    validity_status: ValidityStatus
    source_url: str | None


class SearchResult(BaseModel):
    chunk_id: int
    # RRF 融合分数，只用于排序
    score: float
    # 两路检索各自的名次与原始分数；未进入该路候选时为 null
    dense_rank: int | None
    dense_score: float | None
    keyword_rank: int | None
    keyword_score: float | None
    visual_rank: int | None = None
    visual_score: float | None = None
    document: SearchDocument
    version: SearchVersion
    ordinal: int
    chapter: str
    section: str
    article: str
    heading: str
    content: str
    # PDF 物理页序号；无分页格式为 null
    page_start: int | None
    page_end: int | None
    # 页面上的位置 [x0, y0, x1, y1]（PDF 点）；方案不提供时为 null
    bbox: list[float] | None = None
    # text / table；方案不提供时为 null
    modality: str | None = None


class SearchResponse(BaseModel):
    query: str
    pipeline: str
    results: list[SearchResult]
