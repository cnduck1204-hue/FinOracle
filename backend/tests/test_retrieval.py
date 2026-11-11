import asyncio
import threading
from uuid import uuid4

import pytest

from app.core.config import VectorStoreSettings
from app.rag.indexing import MilvusVectorStore, SearchHit, VectorRecord
from app.rag.indexing.errors import embedding_unavailable
from app.rag.retrieval import HybridRetriever, RetrievedChunk, reciprocal_rank_fusion
from tests.worker_support import FakeEmbedding, MemoryVectorStore


def hits(*chunk_ids: int) -> list[SearchHit]:
    return [
        SearchHit(chunk_id, 1.0 / rank) for rank, chunk_id in enumerate(chunk_ids, 1)
    ]


def test_rrf_rewards_agreement_between_paths():
    fused = reciprocal_rank_fusion(hits(1, 2, 3), hits(3, 2, 4), limit=10, k=60)

    # 3：1/63 + 1/61 ≈ 0.032266，略高于 2：2/62 ≈ 0.032258；只在一路出现的排在后面
    assert [item.chunk_id for item in fused] == [3, 2, 1, 4]
    assert fused[1] == RetrievedChunk(
        chunk_id=2,
        score=pytest.approx(2 / 62),
        dense_rank=2,
        dense_score=0.5,
        keyword_rank=2,
        keyword_score=0.5,
    )
    only_dense = fused[2]
    assert (only_dense.keyword_rank, only_dense.keyword_score) == (None, None)


def test_rrf_breaks_ties_by_best_rank_then_chunk_id_and_applies_the_limit():
    # 1 与 9 分数相同（各在一路排第 1）；5 在两路都排第 2
    fused = reciprocal_rank_fusion(hits(9, 5), hits(1, 5), limit=3, k=60)
    assert [item.chunk_id for item in fused] == [5, 1, 9]
    assert reciprocal_rank_fusion(hits(1, 2), [], limit=1)[0].chunk_id == 1
    assert reciprocal_rank_fusion([], [], limit=5) == []


def test_rrf_ignores_duplicate_hits_within_a_path():
    fused = reciprocal_rank_fusion(hits(7, 7), [], limit=5, k=60)
    assert fused == [RetrievedChunk(7, pytest.approx(1 / 61), 1, 1.0)]


def store_with(*texts, version=None):
    store = MemoryVectorStore()
    version = version or uuid4()
    embedding = FakeEmbedding()
    records = []
    for chunk_id, text in enumerate(texts, 1):
        vector = [float(len(text)), 1.0, 0.0, 0.0]
        records.append(VectorRecord(chunk_id, uuid4(), version, text, vector))
    store.upsert(records)
    return store, embedding, version


def test_retriever_fuses_dense_and_keyword_paths():
    store, embedding, version = store_with(
        "证券公司风险处置", "支付机构监督", "证券登记结算"
    )
    retriever = HybridRetriever(embedding, store, candidates=2)

    results = asyncio.run(retriever.retrieve("证券", version_ids=[version], limit=5))

    assert {item.chunk_id for item in results} <= {1, 2, 3}
    assert all(item.score > 0 for item in results)
    keyword_ranked = [item.chunk_id for item in results if item.keyword_rank]
    assert set(keyword_ranked) == {1, 3}
    # 查询只向量化一次，每路只取 candidates 个候选
    assert embedding.batches == [["证券"]]
    assert sum(1 for item in results if item.dense_rank) <= 2


def test_retriever_skips_everything_without_published_versions():
    store, embedding, _ = store_with("内容")
    store.unavailable = True
    retriever = HybridRetriever(embedding, store)
    assert asyncio.run(retriever.retrieve("问题", version_ids=[], limit=5)) == []
    assert embedding.batches == []


def test_keyword_search_runs_while_the_query_is_embedded():
    store, embedding, version = store_with("证券")
    started = threading.Event()
    search_keywords = store.search_keywords

    def tracked(*args, **kwargs):
        started.set()
        return search_keywords(*args, **kwargs)

    store.search_keywords = tracked
    embed = embedding.embed_documents

    async def slow_embed(texts):
        # 关键词检索不等向量化完成就开始
        for _ in range(100):
            if started.is_set():
                break
            await asyncio.sleep(0.01)
        assert started.is_set()
        return await embed(texts)

    embedding.embed_documents = slow_embed
    retriever = HybridRetriever(embedding, store)
    assert asyncio.run(retriever.retrieve("证券", version_ids=[version], limit=1))


def test_embedding_failure_propagates_and_stops_waiting_for_keywords():
    store, embedding, version = store_with("证券")
    embedding.fail_with = embedding_unavailable()
    retriever = HybridRetriever(embedding, store)
    with pytest.raises(Exception) as caught:
        asyncio.run(retriever.retrieve("证券", version_ids=[version], limit=1))
    assert caught.value.code == "EMBEDDING_UNAVAILABLE"


def test_embed_query_uses_the_document_request():
    embedding = FakeEmbedding()
    assert asyncio.run(embedding.embed_query("问题")) == [2.0, 1.0, 0.0, 0.0]
    assert embedding.batches == [["问题"]]


class SearchingMilvus:
    def __init__(self, exists=True):
        self.exists = exists
        self.searches = []

    def has_collection(self, name):
        return self.exists

    def search(self, collection, **kwargs):
        self.searches.append(kwargs)
        return [
            [
                {"id": 99, "distance": 0.9, "entity": {"chunk_id": 12}},
                {"id": 98, "distance": 0.4, "entity": {"chunk_id": 7}},
            ]
        ]


def milvus(client):
    return MilvusVectorStore(
        VectorStoreSettings(_env_file=None),
        model="text-embedding-v4",
        dimension=4,
        client_factory=lambda: client,
    )


def test_milvus_searches_both_paths_inside_the_given_versions():
    client = SearchingMilvus()
    store = milvus(client)
    first, second = uuid4(), uuid4()

    dense = store.search_dense(
        (0.1, 0.2, 0.3, 0.4), version_ids=[first, second], limit=5
    )
    keyword = store.search_keywords("停业整顿", version_ids=[first], limit=3)

    assert dense == [SearchHit(12, 0.9), SearchHit(7, 0.4)] == keyword
    dense_call, keyword_call = client.searches
    assert dense_call["anns_field"] == "dense"
    assert dense_call["data"] == [[0.1, 0.2, 0.3, 0.4]]
    assert dense_call["search_params"] == {"metric_type": "COSINE"}
    assert dense_call["filter"] == f'version_id in ["{first}", "{second}"]'
    assert dense_call["consistency_level"] == "Strong"
    assert keyword_call["anns_field"] == "sparse"
    assert keyword_call["data"] == ["停业整顿"]
    assert keyword_call["search_params"] == {"metric_type": "BM25"}
    assert keyword_call["limit"] == 3


def test_milvus_search_without_versions_or_collection_returns_nothing():
    client = SearchingMilvus(exists=False)
    store = milvus(client)
    assert store.search_dense([0.1] * 4, version_ids=[], limit=5) == []
    assert store.search_keywords("x", version_ids=[uuid4()], limit=5) == []
    assert client.searches == []
