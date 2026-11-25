from datetime import date
from uuid import uuid4

from fastapi.testclient import TestClient

from app.agent.citations import compute_confidence
from app.api.dependencies import require_current_user
from app.main import app
from app.models import Document, DocumentChunk, DocumentVersion
from app.models.document import content_digest
from app.rag.indexing import VectorRecord
from app.schemas.chat import ChatChunkCitation, ChatParagraph
from app.schemas.ingestion import normalize_tags
from app.schemas.search import SearchDocument, SearchVersion
from tests.test_ingestion_api import make_user
from tests.test_search_api import search as search


def _seed_doc(
    search_env,
    *,
    title: str,
    issuer: str = "金融监管总局",
    category: str | None = "银行监管",
    tags: list[str] | None = None,
    status: str = "published",
    validity_status: str = "effective",
    document_number: str | None = None,
    content: str = "商业银行应当建立健全全面风险管理体系。",
    vector: list[float] | None = None,
) -> tuple[str, str, int]:
    with search_env.db() as session:
        doc = Document(title=title, issuer=issuer, category=category, tags=tags)
        session.add(doc)
        session.flush()
        version = DocumentVersion(
            document_id=doc.id,
            status=status,
            document_number=document_number,
            version_note="2024年版",
            effective_date=date(2024, 1, 1),
            validity_status=validity_status,
            source_url="https://example.gov.cn/rule",
            file_name=f"{title}.pdf",
            file_type="pdf",
            size_bytes=512,
            sha256=uuid4().hex + uuid4().hex,
            object_key=f"docs/{uuid4().hex}.pdf",
            parse_options={},
            chunk_count=1,
        )
        session.add(version)
        session.flush()
        chunk = DocumentChunk(
            version_id=version.id,
            document_id=doc.id,
            ordinal=1,
            chapter="第一章",
            section="",
            article="第一条",
            heading="总则",
            content=content,
            content_sha256=content_digest(content),
            page_start=1,
            page_end=1,
            sources=["text"],
        )
        session.add(chunk)
        session.commit()
        if vector is not None:
            search_env.store.upsert(
                [VectorRecord(chunk.id, doc.id, version.id, content, vector)]
            )
        return str(doc.id), str(version.id), chunk.id


def test_library_requires_login():
    client = TestClient(app, raise_server_exceptions=False)
    res = client.get("/api/v1/library")
    assert (res.status_code, res.json()["code"]) == (401, "AUTH_REQUIRED")


def test_library_facets_filtering_and_chunks(search):
    client = search.client
    bank_id, _, _ = _seed_doc(
        search,
        title="商业银行资本管理办法",
        category="银行监管",
        tags=["资本充足率", "巴塞尔协议"],
        validity_status="effective",
        document_number="金规〔2023〕4号",
        content="商业银行核心一级资本充足率不得低于5%。",
    )
    repealed_id, _, _ = _seed_doc(
        search,
        title="商业银行资本管理办法（试行）",
        category="银行监管",
        tags=["资本充足率", "旧版"],
        validity_status="repealed",
        document_number="银监会令2012年第1号",
        content="旧版资本充足率要求。",
    )
    draft_id, _, _ = _seed_doc(
        search,
        title="未发布草稿制度",
        category="保险制度",
        tags=["草稿"],
        status="ready",
    )

    facets = client.get("/api/v1/library/facets").json()
    assert facets["total_chunks"] >= 2
    assert any(
        item["name"] == "银行监管" and item["count"] == 2
        for item in facets["categories"]
    )
    assert any(
        item["name"] == "资本充足率" and item["count"] == 2 for item in facets["tags"]
    )
    assert facets["validity_counts"]["effective"] >= 1
    assert facets["validity_counts"]["repealed"] >= 1

    # Filter by category & tag & hide repealed
    filtered = client.get(
        "/api/v1/library",
        params={
            "category": "银行监管",
            "tag": "资本充足率",
            "include_repealed": "false",
        },
    ).json()
    assert [item["id"] for item in filtered["items"]] == [bank_id]

    # Effective comes before repealed when both included
    both = client.get("/api/v1/library", params={"category": "银行监管"}).json()
    ids = [item["id"] for item in both["items"]]
    assert ids.index(bank_id) < ids.index(repealed_id)

    # Filter by keyword matches document_number
    by_num = client.get("/api/v1/library", params={"q": "金规〔2023〕4号"}).json()
    assert [item["id"] for item in by_num["items"]] == [bank_id]

    # Detail & chunks (with in-document search)
    detail = client.get(f"/api/v1/library/{bank_id}")
    assert detail.status_code == 200
    assert detail.json()["category"] == "银行监管"
    assert detail.json()["tags"] == ["资本充足率", "巴塞尔协议"]

    chunks = client.get(
        f"/api/v1/library/{bank_id}/chunks", params={"q": "核心一级资本"}
    ).json()
    assert chunks["total"] == 1
    assert "5%" in chunks["items"][0]["content"]

    # Unpublished document is hidden from public library
    assert client.get(f"/api/v1/library/{draft_id}").status_code == 404
    assert client.get(f"/api/v1/library/{draft_id}/chunks").status_code == 404


def test_admin_document_category_and_tags_update_and_filter(search):
    with search.db() as session:
        admin = make_user("admin")
        admin.username = "admin_tax"
        admin.email = "admin_tax@example.com"
        session.add(admin)
        session.commit()
    app.dependency_overrides[require_current_user] = lambda: admin

    doc_id, _, _ = _seed_doc(
        search, title="证券公司分类评价规定", category=None, tags=None
    )
    updated = search.client.patch(
        f"/api/v1/admin/documents/{doc_id}",
        json={"category": "证券法规", "tags": ["分类监管", "合规风控", "分类监管"]},
    )
    assert updated.status_code == 200
    assert updated.json()["category"] == "证券法规"
    assert updated.json()["tags"] == ["分类监管", "合规风控"]

    listed = search.client.get(
        "/api/v1/admin/documents",
        params={"category": "证券法规", "tag": "合规风控"},
    ).json()
    assert [item["id"] for item in listed["items"]] == [doc_id]

    assert normalize_tags("银行，证券、保险，银行") == ["银行", "证券", "保险"]


def test_search_filters_repealed_by_default_and_prioritizes_effective(search):
    eff_doc, _, eff_chunk = _seed_doc(
        search,
        title="反洗钱现行办法",
        category="反洗钱",
        tags=["客户尽职调查"],
        validity_status="effective",
        content="反洗钱 客户身份识别 最新有效要求",
        vector=[1, 0, 0, 0],
    )
    rep_doc, _, rep_chunk = _seed_doc(
        search,
        title="反洗钱废止旧规",
        category="反洗钱",
        tags=["旧规"],
        validity_status="repealed",
        content="反洗钱 客户身份识别 旧版废止要求",
        vector=[1, 0, 0, 0],
    )
    unk_doc, _, unk_chunk = _seed_doc(
        search,
        title="反洗钱待核验指引",
        category="反洗钱",
        validity_status="unknown",
        content="反洗钱 客户身份识别 待核验指引",
        vector=[1, 0, 0, 0],
    )

    # Default search excludes repealed and places effective ahead of unknown
    default_res = search.client.post(
        "/api/v1/search",
        json={"query": "反洗钱", "document_ids": [eff_doc, rep_doc, unk_doc]},
    )
    assert default_res.status_code == 200
    hits = default_res.json()["results"]
    hit_ids = [item["chunk_id"] for item in hits]
    assert rep_chunk not in hit_ids
    assert hit_ids[0] == eff_chunk
    assert unk_chunk in hit_ids

    # Explicit include_repealed returns all three, still ordered effective -> unknown -> repealed
    all_res = search.client.post(
        "/api/v1/search",
        json={
            "query": "反洗钱",
            "document_ids": [eff_doc, rep_doc, unk_doc],
            "include_repealed": True,
        },
    )
    all_ids = [item["chunk_id"] for item in all_res.json()["results"]]
    assert all_ids == [eff_chunk, unk_chunk, rep_chunk]

    # Explicit validity_status filter
    only_rep = search.client.post(
        "/api/v1/search",
        json={
            "query": "反洗钱",
            "document_ids": [eff_doc, rep_doc, unk_doc],
            "validity_status": "repealed",
        },
    )
    assert [item["chunk_id"] for item in only_rep.json()["results"]] == [rep_chunk]


def _make_citation(number: int, validity_status: str) -> ChatChunkCitation:
    return ChatChunkCitation(
        number=number,
        chunk_id=number,
        document=SearchDocument(id=uuid4(), title="测试制度", issuer="央行"),
        version=SearchVersion(
            id=uuid4(),
            document_number=None,
            version_note="2024年版",
            published_date=None,
            effective_date=None,
            validity_status=validity_status,  # type: ignore[arg-type]
            source_url=None,
        ),
        chapter="第一章",
        section="",
        article=f"第{number}条",
        heading="",
        page_start=1,
        page_end=1,
    )


def test_answer_confidence_scoring():
    # General non-cited answer omits confidence
    assert (
        compute_confidence("answered", [ChatParagraph(text="你好。", citations=[])], [])
        is None
    )

    # Insufficient evidence -> low
    low = compute_confidence("insufficient_evidence", [], [])
    assert low is not None and low.level == "low" and low.score == 15

    # Grounded answered with two effective citations -> high (100)
    c1 = _make_citation(1, "effective")
    c2 = _make_citation(2, "effective")
    high = compute_confidence(
        "answered", [ChatParagraph(text="符合要求[1][2]", citations=[c1, c2])], [c1, c2]
    )
    assert high is not None and high.level == "high" and high.score == 100

    # Repealed citation significantly lowers score and adds warning reason
    rep = _make_citation(1, "repealed")
    penalized = compute_confidence(
        "answered", [ChatParagraph(text="旧规定[1]", citations=[rep])], [rep]
    )
    assert penalized is not None and penalized.score < 70
    assert any("废止" in reason for reason in penalized.reasons)

    # Partial without citations -> low
    partial_no_cite = compute_confidence(
        "partial", [ChatParagraph(text="说明", citations=[])], []
    )
    assert partial_no_cite is not None and partial_no_cite.level == "low"
