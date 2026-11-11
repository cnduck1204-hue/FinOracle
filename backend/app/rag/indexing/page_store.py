"""Milvus 页面多向量存储，按版本隔离并使用 MAX_SIM 检索。"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from pymilvus import DataType, MilvusClient
from pymilvus.client.embedding_list import EmbeddingList

from app.core.config import VectorStoreSettings
from app.rag.indexing.vector_store import MilvusCollection, collection_name
from app.rag.indexing.visual_embedding import MAX_PAGE_VECTORS, MultiVector


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
            max_capacity=MAX_PAGE_VECTORS,
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
