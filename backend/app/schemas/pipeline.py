"""检索方案的响应模型。"""

from typing import Literal

from pydantic import BaseModel

from app.schemas.ingestion import UtcDateTime


class PipelineResponse(BaseModel):
    name: str
    label: str
    description: str
    # 入库部分的名字；相同时两个方案共用一份索引
    ingest: str
    channels: list[str]
    default: bool
    available: bool
    unavailable_reason: str | None


class VersionPipelineResponse(BaseModel):
    pipeline: PipelineResponse
    status: Literal["not_built", "building", "ready", "failed"]
    # 这个版本的文件格式能否使用该方案
    supported: bool
    unit_count: int
    page_count: int | None
    index_model: str | None
    indexed_at: UtcDateTime | None
