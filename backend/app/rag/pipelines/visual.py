"""视觉通道：页面图像多向量检索（ColPali 系列）。

模型运行在独立的视觉编码服务中，API 和 worker 不依赖 torch。每页是向量库中的一条记录，
保存多个图块向量，查询时按 MAX_SIM 打分。命中是页级的，检索时展开为该页上的切片，
与文本通道在切片粒度上融合；没有切片的页不会出现在结果中。
"""

import asyncio
import logging
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx
import pymupdf
from pymilvus import DataType, MilvusClient
from pymilvus.client.embedding_list import EmbeddingList

from app.core.config import VectorStoreSettings, VisualSettings
from app.rag.indexing import EmbeddingProvider, SearchHit, VectorStore
from app.rag.indexing.vector_store import MilvusCollection, collection_name
from app.rag.pipelines.base import IndexTarget, IngestRun, PageUnits, StoredUnit
from app.rag.pipelines.errors import source_unsupported, visual_unavailable
from app.rag.pipelines.structured import download
from app.rag.retrieval import (
    CANDIDATES_PER_PATH,
    RRF_K,
    HybridRetriever,
    RetrievedChunk,
    reciprocal_rank_fusion,
)

logger = logging.getLogger(__name__)

MultiVector = list[list[float]]
# Milvus 数组字段的容量上限；ColSmol 每页的图块数低于此值
_MAX_PAGE_VECTORS = 4096


class VisualEmbeddingProvider:
    """调用视觉编码服务；未配置地址时 configured 为 False，调用时报错。"""

    def __init__(
        self, settings: VisualSettings, client: httpx.AsyncClient | None = None
    ) -> None:
        self.model = settings.visual_model
        self.dimension = settings.visual_dimension
        self.batch_pages = settings.visual_batch_pages
        self.render_dpi = settings.visual_render_dpi
        self._url = settings.visual_service_url
        self._timeout = settings.visual_timeout_seconds
        self._client = client

    @property
    def configured(self) -> bool:
        return self._url is not None

    @property
    def model_id(self) -> str:
        return f"{self.model}:{self.dimension}"

    async def encode_images(self, images: Sequence[bytes]) -> list[MultiVector]:
        """images 为 PNG；返回每页的图块向量。"""
        files = [
            ("images", (f"{index}.png", data, "image/png"))
            for index, data in enumerate(images)
        ]
        vectors = await self._post("/v1/images", files=files)
        if len(vectors) != len(images):
            raise visual_unavailable("视觉编码结果的数量与请求不一致")
        for page in vectors:
            self._check(page)
        return vectors

    async def encode_query(self, query: str) -> MultiVector:
        (vectors,) = await self._post("/v1/queries", json={"queries": [query]})
        self._check(vectors)
        return vectors

    def _check(self, vectors: MultiVector) -> None:
        if not vectors or any(len(vector) != self.dimension for vector in vectors):
            raise visual_unavailable("视觉编码结果的维度与配置不一致")
        if len(vectors) > _MAX_PAGE_VECTORS:
            raise visual_unavailable("视觉编码结果的向量数超过上限")

    async def _post(self, path: str, **kwargs: Any) -> list[MultiVector]:
        if self._url is None:
            raise visual_unavailable("未配置 VISUAL_SERVICE_URL，无法使用视觉通道")
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout, trust_env=False)
        try:
            response = await self._client.post(f"{self._url}{path}", **kwargs)
        except httpx.HTTPError as error:
            logger.warning("visual_request_failed: %s", type(error).__name__)
            raise visual_unavailable() from error
        if response.status_code != 200:
            logger.warning("visual_service_error: %s", response.status_code)
            raise visual_unavailable()
        try:
            return response.json()["vectors"]
        except (ValueError, KeyError, TypeError) as error:
            raise visual_unavailable("视觉编码服务返回了无法识别的结果") from error

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


@dataclass(frozen=True, slots=True)
class PageRecord:
    document_id: UUID
    version_id: UUID
    page: int
    vectors: MultiVector


@dataclass(frozen=True, slots=True)
class PageHit:
    version_id: UUID
    page: int
    score: float


class PageVectorStore(MilvusCollection):
    """每页一条记录，图块向量存为结构体数组，按 MAX_SIM 检索。"""

    def __init__(
        self,
        settings: VectorStoreSettings,
        *,
        namespace: str,
        model: str,
        dimension: int,
        client_factory: Callable[[], Any] | None = None,
    ) -> None:
        super().__init__(
            settings,
            collection_name(
                settings.milvus_collection_prefix,
                model,
                dimension,
                namespace=f"{namespace}_pages",
            ),
            client_factory,
        )
        self.dimension = dimension

    def upsert(self, records: Sequence[PageRecord]) -> None:
        if not records:
            return
        data = [
            {
                "page_key": f"{record.version_id}:{record.page}",
                "document_id": str(record.document_id),
                "version_id": str(record.version_id),
                "page": record.page,
                "patches": [{"vector": vector} for vector in record.vectors],
            }
            for record in records
        ]
        self._call(lambda client: client.upsert(self.collection, data=data))

    def search(
        self, vectors: MultiVector, *, version_ids: Sequence[UUID], limit: int
    ) -> list[PageHit]:
        if not version_ids:
            return []

        def search(client: Any) -> list[PageHit]:
            if not client.has_collection(self.collection):
                return []
            query = EmbeddingList()
            for vector in vectors:
                query.add(vector)
            results = client.search(
                self.collection,
                data=[query],
                anns_field="patches[vector]",
                limit=limit,
                filter=self._versions_filter(version_ids),
                search_params={"metric_type": "MAX_SIM_COSINE"},
                output_fields=["version_id", "page"],
                consistency_level="Strong",
            )
            return [
                PageHit(
                    UUID(hit["entity"]["version_id"]),
                    int(hit["entity"]["page"]),
                    float(hit["distance"]),
                )
                for hit in results[0]
            ]

        return self._call(search)

    def _schema(self) -> Any:
        schema = MilvusClient.create_schema(auto_id=False, enable_dynamic_field=False)
        # 版本 UUID + 冒号 + 页码
        schema.add_field("page_key", DataType.VARCHAR, is_primary=True, max_length=64)
        schema.add_field("document_id", DataType.VARCHAR, max_length=36)
        schema.add_field("version_id", DataType.VARCHAR, max_length=36)
        schema.add_field("page", DataType.INT64)
        struct = MilvusClient.create_struct_field_schema()
        struct.add_field("vector", DataType.FLOAT_VECTOR, dim=self.dimension)
        schema.add_field(
            "patches",
            DataType.ARRAY,
            element_type=DataType.STRUCT,
            struct_schema=struct,
            max_capacity=_MAX_PAGE_VECTORS,
        )
        return schema

    @staticmethod
    def _index_params(client: Any) -> Any:
        params = client.prepare_index_params()
        params.add_index(
            field_name="patches[vector]",
            index_type="HNSW",
            metric_type="MAX_SIM_COSINE",
            params={"M": 16, "efConstruction": 200},
        )
        params.add_index(field_name="version_id", index_type="INVERTED")
        return params


def render_pages(path: Path, start: int, stop: int, dpi: int) -> list[bytes]:
    """把第 start 到 stop - 1 页（从 0 起）渲染为 PNG。"""
    with pymupdf.open(path) as document:
        return [
            document.load_page(index).get_pixmap(dpi=dpi, alpha=False).tobytes("png")
            for index in range(start, min(stop, document.page_count))
        ]


def page_count(path: Path) -> int:
    with pymupdf.open(path) as document:
        return document.page_count


class VisualIndexer:
    provides = frozenset({"visual"})

    def __init__(
        self, embedding: VisualEmbeddingProvider, store: PageVectorStore
    ) -> None:
        self.embedding = embedding
        self.store = store

    @property
    def model_id(self) -> str:
        return self.embedding.model_id

    async def index(
        self, run: IngestRun, target: IndexTarget, units: Sequence[StoredUnit]
    ) -> None:
        if target.pdf_key is None:
            raise source_unsupported()
        await asyncio.to_thread(self.store.ensure_collection)
        await run.run_owned(self.store.delete_by_version, target.version_id)
        with tempfile.TemporaryDirectory(prefix="finoracle-visual-") as directory:
            path = Path(directory) / "source.pdf"
            await asyncio.to_thread(download, run.storage, target.pdf_key, path)
            total = await asyncio.to_thread(page_count, path)
            run.report("visual", 0, total)
            size = max(1, self.embedding.batch_pages)
            for start in range(0, total, size):
                images = await asyncio.to_thread(
                    render_pages, path, start, start + size, self.embedding.render_dpi
                )
                vectors = await self.embedding.encode_images(images)
                records = [
                    PageRecord(target.document_id, target.version_id, start + i + 1, v)
                    for i, v in enumerate(vectors)
                ]
                await run.run_owned(self.store.upsert, records)
                run.report("visual", start + len(images), total)

    def delete(self, version_id: UUID) -> None:
        self.store.delete_by_version(version_id)


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
        vectors = await self.visual.encode_query(query)
        pages = await asyncio.to_thread(
            self.pages.search, vectors, version_ids=version_ids, limit=self.candidates
        )
        if not pages:
            return []
        ranks = {(hit.version_id, hit.page): i for i, hit in enumerate(pages)}
        scores = {(hit.version_id, hit.page): hit.score for hit in pages}
        units = await page_units(list(ranks))
        # 切片继承所在页的分数，排名靠前的页上的切片在前
        units.sort(key=lambda unit: (ranks[(unit[1], unit[2])], unit[0]))
        return [
            SearchHit(chunk_id, scores[(version_id, page)])
            for chunk_id, version_id, page in units[: self.candidates]
        ]
