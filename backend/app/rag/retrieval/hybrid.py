"""混合检索：稠密向量与 BM25 关键词两路检索，用倒数排名融合（RRF）合并。

两路分数量纲不同（余弦相似度与 BM25），融合只看名次；保留每路的名次与原始分数，
供评测、调参以及后续判断依据是否充分。只在调用方给出的版本（已发布版本）中检索。
"""

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from app.rag.indexing import EmbeddingProvider, SearchHit, VectorStore
from app.rag.models import PageUnits

# 每路检索的候选数与 RRF 常数 k（常用值 60）；属于算法参数，随代码调整与测试
CANDIDATES_PER_PATH = 30
RRF_K = 60


@dataclass(frozen=True, slots=True)
class RetrievedChunk:
    chunk_id: int
    # RRF 融合分数：各路 1 / (k + 名次) 之和，名次从 1 起
    score: float
    dense_rank: int | None = None
    dense_score: float | None = None
    keyword_rank: int | None = None
    keyword_score: float | None = None
    visual_rank: int | None = None
    visual_score: float | None = None


def reciprocal_rank_fusion(
    dense: Sequence[SearchHit],
    keywords: Sequence[SearchHit],
    *,
    limit: int,
    k: int = RRF_K,
    visual: Sequence[SearchHit] = (),
) -> list[RetrievedChunk]:
    """按 RRF 合并各路结果；同分时名次更靠前者优先，再按切片 ID 稳定排序。"""
    entries: dict[int, dict] = {}
    for path, hits in (("dense", dense), ("keyword", keywords), ("visual", visual)):
        for rank, hit in enumerate(hits, start=1):
            entry = entries.setdefault(hit.chunk_id, {"score": 0.0})
            if f"{path}_rank" in entry:
                continue
            entry["score"] += 1 / (k + rank)
            entry[f"{path}_rank"] = rank
            entry[f"{path}_score"] = hit.score
    fused = [
        RetrievedChunk(chunk_id=chunk_id, **entry)
        for chunk_id, entry in entries.items()
    ]

    def best_rank(item: RetrievedChunk) -> int:
        ranks = [
            rank
            for rank in (item.dense_rank, item.keyword_rank, item.visual_rank)
            if rank is not None
        ]
        return min(ranks)

    fused.sort(key=lambda item: (-item.score, best_rank(item), item.chunk_id))
    return fused[:limit]


class HybridRetriever:
    """检索部分的基础实现：稠密向量与 BM25 关键词两路融合。"""

    requires = frozenset({"dense", "keyword"})

    def __init__(
        self,
        embedding: EmbeddingProvider,
        store: VectorStore,
        *,
        candidates: int = CANDIDATES_PER_PATH,
        rrf_k: int = RRF_K,
    ) -> None:
        self.embedding = embedding
        self.store = store
        self.candidates = candidates
        self.rrf_k = rrf_k

    async def retrieve(
        self,
        query: str,
        *,
        version_ids: Sequence[UUID],
        limit: int,
        page_units: PageUnits | None = None,
    ) -> list[RetrievedChunk]:
        if not version_ids:
            return []
        dense, keywords = await self._text_channels(query, version_ids)
        return reciprocal_rank_fusion(dense, keywords, limit=limit, k=self.rrf_k)

    async def _text_channels(
        self, query: str, version_ids: Sequence[UUID]
    ) -> tuple[list[SearchHit], list[SearchHit]]:
        # 关键词检索不依赖查询向量，与向量化同时进行，缩短首次响应时间
        keyword_task = asyncio.create_task(
            asyncio.to_thread(
                self.store.search_keywords,
                query,
                version_ids=version_ids,
                limit=self.candidates,
            )
        )
        try:
            vector = await self.embedding.embed_query(query)
            dense = await asyncio.to_thread(
                self.store.search_dense,
                vector,
                version_ids=version_ids,
                limit=self.candidates,
            )
            keywords = await keyword_task
        finally:
            if not keyword_task.done():
                keyword_task.cancel()
                await asyncio.gather(keyword_task, return_exceptions=True)
        return dense, keywords
