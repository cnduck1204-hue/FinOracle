"""Pipeline 抽象：一个 Pipeline 是从入库到检索的完整流程，由可以独立组合的两部分组成。

- 入库部分（IngestPipeline）= 一个解析器 + 若干索引器，产出切片和索引。索引按入库部分的
  名字隔离，所以两个 Pipeline 可以共用同一份入库结果。
- 检索部分（RetrievalPipeline）声明需要哪些通道，在给定版本中返回排好序的切片。

组合时校验检索部分需要的通道都由入库部分提供，组合错误在启动时暴露。
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Protocol
from uuid import UUID

from app.core.exceptions import AppException
from app.rag.models import (
    IndexTarget,
    IngestRun,
    PageUnits,
    ParsedUnits,
    SourceFile,
    StoredUnit,
)
from app.rag.retrieval import RetrievedChunk

logger = logging.getLogger(__name__)


class UnitParser(Protocol):
    def accepts(self, source: SourceFile) -> bool:
        """能否处理这个文件；提交任务时用于提前拒绝。"""

    async def parse(self, run: IngestRun, source: SourceFile) -> ParsedUnits: ...


class UnitIndexer(Protocol):
    # 提供的检索通道，如 dense、keyword、visual
    provides: frozenset[str]
    # 记录在版本索引上的模型标识；不同时须重建
    model_id: str

    async def index(
        self, run: IngestRun, target: IndexTarget, units: Sequence[StoredUnit]
    ) -> None: ...

    def delete(self, version_id: UUID) -> None:
        """同步网络调用，由调用方放到线程中。"""


@dataclass(frozen=True, slots=True)
class IngestPipeline:
    name: str
    parser: UnitParser
    indexers: tuple[UnitIndexer, ...]

    @property
    def provides(self) -> frozenset[str]:
        return frozenset().union(*(indexer.provides for indexer in self.indexers))

    @property
    def model_id(self) -> str:
        return "+".join(indexer.model_id for indexer in self.indexers)

    async def build(
        self, run: IngestRun, target: IndexTarget, units: Sequence[StoredUnit]
    ) -> None:
        for indexer in self.indexers:
            await indexer.index(run, target, units)

    def delete_quietly(self, version_id: UUID) -> None:
        """尽力删除索引；失败只留下孤儿向量，检索只读已发布版本，不受影响。"""
        for indexer in self.indexers:
            try:
                indexer.delete(version_id)
            except AppException:
                logger.warning("index_cleanup_failed: %s %s", self.name, version_id)


class RetrievalPipeline(Protocol):
    requires: frozenset[str]

    async def retrieve(
        self,
        query: str,
        *,
        version_ids: Sequence[UUID],
        limit: int,
        page_units: PageUnits | None = None,
    ) -> list[RetrievedChunk]: ...


@dataclass(frozen=True, slots=True)
class Pipeline:
    name: str
    label: str
    description: str
    ingest: IngestPipeline
    retrieval: RetrievalPipeline
    # 不为空时该方案当前不可用，内容是原因
    unavailable_reason: str | None = None

    def __post_init__(self) -> None:
        missing = self.retrieval.requires - self.ingest.provides
        if missing:
            raise ValueError(
                f"Pipeline {self.name} 的入库部分缺少通道：{sorted(missing)}"
            )

    def with_retrieval(self, retrieval: RetrievalPipeline) -> "Pipeline":
        return replace(self, retrieval=retrieval)


__all__ = [
    "IndexTarget",
    "IngestRun",
    "PageUnits",
    "ParsedUnits",
    "SourceFile",
    "StoredUnit",
    "UnitParser",
    "UnitIndexer",
    "IngestPipeline",
    "RetrievalPipeline",
    "Pipeline",
]
