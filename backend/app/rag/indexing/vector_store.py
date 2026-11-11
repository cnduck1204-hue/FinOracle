"""VectorStore：按切片 ID 保存向量，按版本删除。检索时按已发布版本过滤，见入库方案第 7 节。

方法均为同步网络调用：worker 经 asyncio.to_thread、API 经 run_in_threadpool 调用。
"""

import logging
import os
import re
import threading
from abc import ABC, abstractmethod
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

# pymilvus 导入时调用 load_dotenv()，会从安装目录向上找到 backend/.env，把其中的密钥全部写入
# 进程环境变量并传给子进程。用 python-dotenv 的开关关闭它；应用配置由 pydantic-settings
# 直接读取 .env，不受影响
os.environ.setdefault("PYTHON_DOTENV_DISABLED", "1")

from pymilvus import (  # noqa: E402
    DataType,
    Function,
    FunctionType,
    MilvusClient,
    MilvusException,
)

from app.core.config import VectorStoreSettings
from app.rag.indexing.errors import vector_store_unavailable

logger = logging.getLogger(__name__)

# VARCHAR 上限；向量化文本已截断到 6000 字（中文约 18 KB）
_TEXT_MAX_LENGTH = 65535


@dataclass(frozen=True, slots=True)
class VectorRecord:
    chunk_id: int
    document_id: UUID
    version_id: UUID
    # 向量化所用文本，同时供 BM25 关键词检索
    text: str
    vector: list[float]


@dataclass(frozen=True, slots=True)
class SearchHit:
    chunk_id: int
    # 稠密向量为余弦相似度，关键词为 BM25 分数；两者量纲不同，融合时只用名次
    score: float


class VectorStore(ABC):
    @abstractmethod
    def ensure_collection(self) -> None:
        """集合不存在时创建并加载；可重复调用。"""

    @abstractmethod
    def upsert(self, records: Sequence[VectorRecord]) -> None:
        """按切片 ID 写入或覆盖。"""

    @abstractmethod
    def delete_by_version(self, version_id: UUID) -> None:
        """删除版本的全部向量；集合或数据不存在时视为已删除。"""

    @abstractmethod
    def search_dense(
        self, vector: Sequence[float], *, version_ids: Sequence[UUID], limit: int
    ) -> list[SearchHit]:
        """在指定版本中按向量相似度检索，按分数从高到低。"""

    @abstractmethod
    def search_keywords(
        self, text: str, *, version_ids: Sequence[UUID], limit: int
    ) -> list[SearchHit]:
        """在指定版本中按 BM25 关键词检索（中文分词），按分数从高到低。"""


def _slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_").lower()


def collection_name(
    prefix: str, model: str, dimension: int, *, namespace: str = ""
) -> str:
    """不同模型或维度的向量互不兼容，各用一个集合；附加入库方案用 namespace 隔离。"""
    name = f"{prefix}_{_slug(model)}_{dimension}"
    return f"{name}_{_slug(namespace)}" if namespace else name


class MilvusCollection:
    """一个 Milvus 集合的共用操作；子类给出字段与索引。"""

    def __init__(
        self,
        settings: VectorStoreSettings,
        collection: str,
        client_factory: Callable[[], Any] | None = None,
    ) -> None:
        self.collection = collection
        token = (
            settings.milvus_token.get_secret_value() if settings.milvus_token else ""
        )
        self._factory = client_factory or (
            lambda: MilvusClient(
                uri=settings.milvus_uri,
                token=token,
                timeout=settings.milvus_timeout_seconds,
            )
        )
        self._client: Any = None
        # 两路检索在两个线程中并发调用，客户端只创建一次
        self._client_lock = threading.Lock()

    def ensure_collection(self) -> None:
        def ensure(client: Any) -> None:
            if not client.has_collection(self.collection):
                try:
                    client.create_collection(
                        collection_name=self.collection,
                        schema=self._schema(),
                        index_params=self._index_params(client),
                    )
                    logger.info("vector_collection_created: %s", self.collection)
                except MilvusException:
                    # 另一个进程同时创建了集合
                    if not client.has_collection(self.collection):
                        raise
            client.load_collection(self.collection)

        self._call(ensure)

    def delete_by_version(self, version_id: UUID) -> None:
        def delete(client: Any) -> None:
            if client.has_collection(self.collection):
                client.delete(self.collection, filter=_version_filter(version_id))

        self._call(delete)

    def _call(self, operation: Callable[[Any], Any]) -> Any:
        try:
            if self._client is None:
                with self._client_lock:
                    if self._client is None:
                        self._client = self._factory()
            return operation(self._client)
        except (MilvusException, OSError) as error:
            logger.warning("vector_store_failed: %s", type(error).__name__)
            raise vector_store_unavailable() from error

    @staticmethod
    def _versions_filter(version_ids: Sequence[UUID]) -> str:
        return _versions_filter(version_ids)

    def _schema(self) -> Any:
        raise NotImplementedError

    @staticmethod
    def _index_params(client: Any) -> Any:
        raise NotImplementedError


class MilvusVectorStore(MilvusCollection, VectorStore):
    """稠密向量用余弦相似度；text 字段经中文分词生成 BM25 稀疏向量，供混合检索。"""

    def __init__(
        self,
        settings: VectorStoreSettings,
        *,
        model: str,
        dimension: int,
        client_factory: Callable[[], Any] | None = None,
        namespace: str = "",
    ) -> None:
        super().__init__(
            settings,
            collection_name(
                settings.milvus_collection_prefix,
                model,
                dimension,
                namespace=namespace,
            ),
            client_factory,
        )
        self.dimension = dimension

    def upsert(self, records: Sequence[VectorRecord]) -> None:
        if not records:
            return
        data = [
            {
                "chunk_id": record.chunk_id,
                "document_id": str(record.document_id),
                "version_id": str(record.version_id),
                "text": record.text,
                "dense": record.vector,
            }
            for record in records
        ]
        self._call(lambda client: client.upsert(self.collection, data=data))

    def search_dense(
        self, vector: Sequence[float], *, version_ids: Sequence[UUID], limit: int
    ) -> list[SearchHit]:
        return self._search(
            data=list(vector),
            field="dense",
            metric="COSINE",
            version_ids=version_ids,
            limit=limit,
        )

    def search_keywords(
        self, text: str, *, version_ids: Sequence[UUID], limit: int
    ) -> list[SearchHit]:
        return self._search(
            data=text,
            field="sparse",
            metric="BM25",
            version_ids=version_ids,
            limit=limit,
        )

    def _search(
        self,
        *,
        data: Any,
        field: str,
        metric: str,
        version_ids: Sequence[UUID],
        limit: int,
    ) -> list[SearchHit]:
        if not version_ids:
            return []

        def search(client: Any) -> list[SearchHit]:
            if not client.has_collection(self.collection):
                return []
            results = client.search(
                self.collection,
                data=[data],
                anns_field=field,
                limit=limit,
                filter=_versions_filter(version_ids),
                search_params={"metric_type": metric},
                output_fields=["chunk_id"],
                # 强一致：刚发布的版本立即可检索；单机实测仅多几毫秒
                consistency_level="Strong",
            )
            return [
                SearchHit(int(hit["entity"]["chunk_id"]), float(hit["distance"]))
                for hit in results[0]
            ]

        return self._call(search)

    def count_by_version(self, version_id: UUID) -> int:
        """强一致地统计版本的向量数，供校验与测试。"""

        def count(client: Any) -> int:
            if not client.has_collection(self.collection):
                return 0
            rows = client.query(
                self.collection,
                filter=_version_filter(version_id),
                output_fields=["count(*)"],
                consistency_level="Strong",
            )
            return int(rows[0]["count(*)"]) if rows else 0

        return self._call(count)

    def _schema(self) -> Any:
        schema = MilvusClient.create_schema(auto_id=False, enable_dynamic_field=False)
        schema.add_field("chunk_id", DataType.INT64, is_primary=True)
        schema.add_field("document_id", DataType.VARCHAR, max_length=36)
        schema.add_field("version_id", DataType.VARCHAR, max_length=36)
        schema.add_field(
            "text",
            DataType.VARCHAR,
            max_length=_TEXT_MAX_LENGTH,
            enable_analyzer=True,
            analyzer_params={"type": "chinese"},
        )
        schema.add_field("dense", DataType.FLOAT_VECTOR, dim=self.dimension)
        schema.add_field("sparse", DataType.SPARSE_FLOAT_VECTOR)
        schema.add_function(
            Function(
                name="text_bm25",
                function_type=FunctionType.BM25,
                input_field_names=["text"],
                output_field_names=["sparse"],
            )
        )
        return schema

    @staticmethod
    def _index_params(client: Any) -> Any:
        params = client.prepare_index_params()
        params.add_index(
            field_name="dense", index_type="AUTOINDEX", metric_type="COSINE"
        )
        params.add_index(
            field_name="sparse", index_type="SPARSE_INVERTED_INDEX", metric_type="BM25"
        )
        # 检索与删除都按版本过滤
        params.add_index(field_name="version_id", index_type="INVERTED")
        return params


def _version_filter(version_id: UUID) -> str:
    # UUID 只含十六进制字符与连字符，可直接写入过滤表达式
    return f'version_id == "{UUID(str(version_id))}"'


def _versions_filter(version_ids: Sequence[UUID]) -> str:
    quoted = ", ".join(f'"{UUID(str(version_id))}"' for version_id in version_ids)
    return f"version_id in [{quoted}]"
