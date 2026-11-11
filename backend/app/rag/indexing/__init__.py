"""向量化与向量库：EmbeddingProvider 把切片转为向量，VectorStore 保存并按版本管理。

两类实现均按配置创建；离线入库与在线检索使用同一模型、维度与集合。
"""

from functools import lru_cache

from app.core.config import get_embedding_settings, get_vector_store_settings
from app.rag.indexing.embedding import DashScopeEmbedding, EmbeddingProvider
from app.rag.indexing.text import MAX_INDEX_TEXT_CHARS, index_entries, index_text
from app.rag.indexing.vector_store import (
    MilvusVectorStore,
    SearchHit,
    VectorRecord,
    VectorStore,
    collection_name,
)


def create_embedding() -> EmbeddingProvider:
    return DashScopeEmbedding(get_embedding_settings())


@lru_cache
def get_query_embedding() -> EmbeddingProvider:
    """API 进程内共用的查询向量化客户端，在应用关闭时释放。"""
    return create_embedding()


async def close_query_embedding() -> None:
    if get_query_embedding.cache_info().currsize:
        await get_query_embedding().aclose()


@lru_cache
def get_vector_store() -> VectorStore:
    """进程内共用一个客户端；集合由向量化模型与维度决定。"""
    embedding = get_embedding_settings()
    return MilvusVectorStore(
        get_vector_store_settings(),
        model=embedding.embedding_model,
        dimension=embedding.embedding_dimension,
    )


__all__ = [
    "MAX_INDEX_TEXT_CHARS",
    "DashScopeEmbedding",
    "EmbeddingProvider",
    "MilvusVectorStore",
    "SearchHit",
    "VectorRecord",
    "VectorStore",
    "close_query_embedding",
    "collection_name",
    "create_embedding",
    "get_query_embedding",
    "get_vector_store",
    "index_entries",
    "index_text",
]
