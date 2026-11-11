"""页面视觉召回，以及视觉与文本通道的排名融合。"""

import asyncio
from collections.abc import Sequence
from uuid import UUID

from app.rag.indexing import EmbeddingProvider, SearchHit, VectorStore
from app.rag.indexing.page_store import PageVectorStore
from app.rag.indexing.visual_embedding import VisualEmbeddingProvider
from app.rag.models import PageUnits
from app.rag.retrieval.hybrid import (
    CANDIDATES_PER_PATH,
    RRF_K,
    HybridRetriever,
    RetrievedChunk,
    reciprocal_rank_fusion,
)


async def visual_channel(
    visual: VisualEmbeddingProvider,
    pages: PageVectorStore,
    query: str,
    version_ids: Sequence[UUID],
    page_units: PageUnits,
    candidates: int,
) -> list[SearchHit]:
    """页级命中展开为切片，切片继承所在页的分数，排名靠前的页上的切片在前。"""
    vectors = await visual.encode_query(query)
    hits = await asyncio.to_thread(
        pages.search, vectors, version_ids=version_ids, limit=candidates
    )
    if not hits:
        return []
    ranks = {(hit.version_id, hit.page): i for i, hit in enumerate(hits)}
    scores = {(hit.version_id, hit.page): hit.score for hit in hits}
    units = await page_units(list(ranks))
    units.sort(key=lambda unit: (ranks[(unit[1], unit[2])], unit[0]))
    return [
        SearchHit(chunk_id, scores[(version_id, page)])
        for chunk_id, version_id, page in units[:candidates]
    ]


class VisualHybridRetriever(HybridRetriever):
    """稠密向量、BM25 与页面视觉三路融合。"""

    requires = frozenset({"dense", "keyword", "visual"})

    def __init__(
        self,
        embedding: EmbeddingProvider,
        store: VectorStore,
        visual: VisualEmbeddingProvider,
        pages: PageVectorStore,
        *,
        candidates: int = CANDIDATES_PER_PATH,
        rrf_k: int = RRF_K,
    ) -> None:
        super().__init__(embedding, store, candidates=candidates, rrf_k=rrf_k)
        self.visual = visual
        self.pages = pages

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
        if page_units is None:
            raise ValueError("视觉通道需要 page_units 把页面展开为切片")
        visual_task = asyncio.create_task(
            self._visual_hits(query, version_ids, page_units)
        )
        try:
            dense, keywords = await self._text_channels(query, version_ids)
            visual = await visual_task
        finally:
            if not visual_task.done():
                visual_task.cancel()
                await asyncio.gather(visual_task, return_exceptions=True)
        return reciprocal_rank_fusion(
            dense, keywords, visual=visual, limit=limit, k=self.rrf_k
        )

    async def _visual_hits(
        self,
        query: str,
        version_ids: Sequence[UUID],
        page_units: PageUnits,
    ) -> list[SearchHit]:
        return await visual_channel(
            self.visual, self.pages, query, version_ids, page_units, self.candidates
        )


class VisualOnlyRetriever:
    """只用页面视觉检索，结果是页面本身。"""

    requires = frozenset({"visual"})

    def __init__(
        self,
        visual: VisualEmbeddingProvider,
        pages: PageVectorStore,
        *,
        candidates: int = CANDIDATES_PER_PATH,
        rrf_k: int = RRF_K,
    ) -> None:
        self.visual = visual
        self.pages = pages
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
        if page_units is None:
            raise ValueError("视觉通道需要 page_units 把页面展开为切片")
        hits = await visual_channel(
            self.visual, self.pages, query, version_ids, page_units, self.candidates
        )
        return reciprocal_rank_fusion([], [], visual=hits, limit=limit, k=self.rrf_k)
