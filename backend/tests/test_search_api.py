import asyncio
from datetime import date
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

import app.rag.indexing as indexing
from app.api.dependencies import require_current_user
from app.api.v1.endpoints.search import get_retriever
from app.db.base import Base
from app.db.engine import create_session_factory
from app.db.session import get_db
from app.main import app
from app.models import Document, DocumentChunk, DocumentVersion
from app.models.document import content_digest
from app.rag.indexing import VectorRecord
from app.rag.indexing.errors import (
    embedding_auth_failed,
    embedding_rejected,
    embedding_unavailable,
)
from app.rag.retrieval import HybridRetriever, RetrievedChunk
from tests.test_ingestion_api import enable_foreign_keys, make_user
from tests.worker_support import FakeEmbedding, MemoryVectorStore

URL = "/api/v1/search"


@pytest.fixture
def search(tmp_path):
    path = tmp_path / "search.db"
    sync_engine = create_engine(f"sqlite:///{path}")
    event.listen(sync_engine, "connect", enable_foreign_keys)
    Base.metadata.create_all(sync_engine)
    store = MemoryVectorStore()
    ids = SimpleNamespace()
    next_chunk = iter(range(1, 100))

    def add(session, title, status, articles, **version_values):
        document = Document(title=title, issuer="国务院")
        session.add(document)
        session.flush()
        version = DocumentVersion(
            document_id=document.id,
            status=status,
            file_name="条例.pdf",
            file_type="pdf",
            size_bytes=1,
            sha256=uuid4().hex * 2,
            object_key="k",
            parse_options={},
            **version_values,
        )
        session.add(version)
        session.flush()
        chunk_ids = []
        for ordinal, (article, content) in enumerate(articles, 1):
            chunk = DocumentChunk(
                id=next(next_chunk),
                version_id=version.id,
                document_id=document.id,
                ordinal=ordinal,
                chapter="第一章 总则",
                article=article,
                content=content,
                content_sha256=content_digest(content),
                page_start=ordinal,
                page_end=ordinal,
                sources=["text"],
            )
            session.add(chunk)
            chunk_ids.append(chunk.id)
            text = f"{title} {article}\n{content}"
            store.upsert(
                [
                    VectorRecord(
                        chunk.id,
                        document.id,
                        version.id,
                        text,
                        [float(len(text)), 1.0, 0.0, 0.0],
                    )
                ]
            )
        return document, version, chunk_ids

    with Session(sync_engine, expire_on_commit=False) as session:
        ids.securities, ids.current, ids.current_chunks = add(
            session,
            "证券公司风险处置条例",
            "published",
            [
                ("第一条", "为了控制和化解证券公司风险，制定本条例。"),
                ("第六条", "发现证券公司存在重大风险隐患，可以进行专项检查。"),
            ],
            version_note="2023年第二次修订",
            published_date=date(2023, 7, 20),
            source_url="https://example.com/523",
        )
        session.add(
            DocumentVersion(
                document_id=ids.securities.id,
                status="archived",
                file_name="旧.pdf",
                file_type="pdf",
                size_bytes=1,
                sha256="a" * 64,
                object_key="k",
                parse_options={},
            )
        )
        session.flush()
        archived = session.query(DocumentVersion).filter_by(status="archived").one()
        store.upsert(
            [
                VectorRecord(
                    90, ids.securities.id, archived.id, "证券公司 旧版", [1, 1, 0, 0]
                )
            ]
        )
        ids.payment, _, ids.payment_chunks = add(
            session,
            "非银行支付机构监督管理条例",
            "published",
            [("第一条", "为了规范非银行支付机构行为，防范支付风险。")],
        )
        ids.draft, _, ids.draft_chunks = add(
            session, "证券登记结算办法", "ready", [("第一条", "证券登记结算。")]
        )
        session.commit()

    engine = create_async_engine(f"sqlite+aiosqlite:///{path}", poolclass=NullPool)
    event.listen(engine.sync_engine, "connect", enable_foreign_keys)
    sessions = create_session_factory(engine)
    embedding = FakeEmbedding()
    state = SimpleNamespace(retriever=HybridRetriever(embedding, store))

    async def database():
        async with sessions() as session:
            yield session

    async def identity():
        return make_user("user")

    previous = app.dependency_overrides.copy()
    app.dependency_overrides[get_db] = database
    app.dependency_overrides[require_current_user] = identity
    app.dependency_overrides[get_retriever] = lambda: state.retriever
    client = TestClient(app, raise_server_exceptions=False)
    try:
        yield SimpleNamespace(
            client=client,
            ids=ids,
            store=store,
            embedding=embedding,
            state=state,
            db=lambda: Session(sync_engine, expire_on_commit=False),
        )
    finally:
        client.close()
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)
        asyncio.run(engine.dispose())
        sync_engine.dispose()


def test_search_requires_login():
    client = TestClient(app, raise_server_exceptions=False)
    response = client.post(URL, json={"query": "证券"})
    assert (response.status_code, response.json()["code"]) == (401, "AUTH_REQUIRED")


def test_results_cover_only_published_versions_with_citation_details(search):
    response = search.client.post(URL, json={"query": "证券公司风险"})

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    body = response.json()
    assert body["query"] == "证券公司风险"
    returned = [item["chunk_id"] for item in body["results"]]
    assert set(returned) <= {*search.ids.current_chunks, *search.ids.payment_chunks}
    assert set(search.ids.current_chunks) <= set(returned)
    assert not {90, *search.ids.draft_chunks} & set(returned)

    first = next(
        i for i in body["results"] if i["chunk_id"] == search.ids.current_chunks[1]
    )
    assert first["document"] == {
        "id": str(search.ids.securities.id),
        "title": "证券公司风险处置条例",
        "issuer": "国务院",
    }
    assert first["version"] == {
        "id": str(search.ids.current.id),
        "document_number": None,
        "version_note": "2023年第二次修订",
        "published_date": "2023-07-20",
        "effective_date": None,
        "validity_status": "unknown",
        "source_url": "https://example.com/523",
    }
    assert (first["article"], first["chapter"], first["ordinal"]) == (
        "第六条",
        "第一章 总则",
        2,
    )
    assert (first["page_start"], first["page_end"]) == (2, 2)
    assert first["keyword_rank"] is not None and first["dense_rank"] is not None
    scores = [item["score"] for item in body["results"]]
    assert scores == sorted(scores, reverse=True)


def test_search_can_be_limited_to_selected_documents(search):
    response = search.client.post(
        URL,
        json={
            "query": "风险",
            "document_ids": [str(search.ids.payment.id)],
            "top_k": 5,
        },
    )
    assert [item["chunk_id"] for item in response.json()["results"]] == (
        search.ids.payment_chunks
    )

    unpublished = search.client.post(
        URL, json={"query": "证券", "document_ids": [str(search.ids.draft.id)]}
    )
    assert unpublished.json()["results"] == []
    assert search.embedding.batches == [["风险"]]


def test_top_k_limits_the_results(search):
    response = search.client.post(URL, json={"query": "证券公司风险", "top_k": 1})
    assert len(response.json()["results"]) == 1


def test_nothing_published_means_no_external_calls(search):
    with search.db() as session:
        for version in session.query(DocumentVersion):
            version.status = "archived"
        session.commit()
    search.store.unavailable = True

    response = search.client.post(URL, json={"query": "证券"})

    assert (response.status_code, response.json()["results"]) == (200, [])
    assert search.embedding.batches == []


@pytest.mark.parametrize(
    "body",
    [
        {"query": "   "},
        {"query": "x" * 1001},
        {"query": "证券", "top_k": 0},
        {"query": "证券", "top_k": 21},
        {"query": "证券", "document_ids": ["not-a-uuid"]},
        {"query": "证券", "document_ids": [str(uuid4()) for _ in range(51)]},
        {"query": "证券", "mode": "dense"},
    ],
)
def test_invalid_requests_are_rejected(search, body):
    response = search.client.post(URL, json=body)
    assert (response.status_code, response.json()["code"]) == (
        422,
        "REQUEST_VALIDATION_ERROR",
    )
    assert search.embedding.batches == []


@pytest.mark.parametrize("fail", ["embedding_auth", "embedding_down", "vector_store"])
def test_dependency_failures_become_search_unavailable(search, fail, caplog):
    if fail == "vector_store":
        search.store.unavailable = True
    else:
        search.embedding.fail_with = (
            embedding_auth_failed()
            if fail == "embedding_auth"
            else embedding_unavailable()
        )
    response = search.client.post(URL, json={"query": "证券"})
    assert (response.status_code, response.json()["code"]) == (
        503,
        "SEARCH_UNAVAILABLE",
    )
    assert "证券" not in caplog.text


def test_rejected_queries_keep_their_own_error(search):
    search.embedding.fail_with = embedding_rejected("向量化请求被拒绝（HTTP 400）")
    response = search.client.post(URL, json={"query": "证券"})
    assert (response.status_code, response.json()["code"]) == (
        422,
        "EMBEDDING_REJECTED",
    )


def test_chunks_withdrawn_during_retrieval_are_dropped(search):
    kept = search.ids.payment_chunks[0]

    class Stale:
        requires = HybridRetriever.requires

        async def retrieve(self, query, *, version_ids, limit, page_units=None):
            return [RetrievedChunk(90, 0.5, 1, 0.9), RetrievedChunk(kept, 0.4, 2, 0.8)]

    search.state.retriever = Stale()
    response = search.client.post(URL, json={"query": "证券"})
    assert [item["chunk_id"] for item in response.json()["results"]] == [kept]


def test_query_embedding_client_is_closed_on_shutdown(monkeypatch):
    closed = []

    class Closing(FakeEmbedding):
        async def aclose(self):
            closed.append(True)

    monkeypatch.setattr(indexing, "create_embedding", Closing)
    indexing.get_query_embedding.cache_clear()
    try:
        asyncio.run(indexing.close_query_embedding())
        assert closed == []
        assert indexing.get_query_embedding() is indexing.get_query_embedding()
        asyncio.run(indexing.close_query_embedding())
        assert closed == [True]
    finally:
        indexing.get_query_embedding.cache_clear()
