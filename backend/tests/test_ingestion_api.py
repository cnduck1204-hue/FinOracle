import asyncio
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from app.api.dependencies import require_current_user
from app.api.v1.endpoints.search import get_retriever
from app.core.config import ParsingSettings
from app.db.base import Base
from app.db.engine import create_session_factory
from app.db.session import get_db
from app.main import app
from app.models import Document, DocumentChunk, DocumentVersion, ImportJob, User
from app.rag.indexing import get_vector_store
from app.rag.retrieval import HybridRetriever
from app.repositories.documents import DocumentVersionRepository
from app.services import document_imports, document_publishing
from app.services.document_imports import clean_file_name
from app.services.document_library import content_disposition, media_type_for
from app.storage import get_storage
from app.storage.keys import blocks_key, original_key
from app.worker.index import IndexHandler
from app.worker.parse import ParseHandler
from app.worker.runtime import Worker
from tests.parsing_samples import make_pdf
from tests.worker_support import FakeEmbedding, MemoryStorage, MemoryVectorStore

API = "/api/v1/admin"
REGULATION = (
    "第一章 总则\n\n"
    "第一条 为了规范金融机构经营行为，制定本办法。\n\n"
    "第二条 本办法适用于境内金融机构。\n\n"
    "第三条 本办法自公布之日起施行。\n"
).encode()
NO_OCR = {"ocr": "none"}


def enable_foreign_keys(connection, _record):
    cursor = connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def make_user(role: str) -> User:
    now = datetime.now(UTC).replace(tzinfo=None)
    return User(
        id=uuid4(),
        username=f"{role}_01",
        email=f"{role}@example.com",
        password_hash="private-hash",
        role=role,
        status="active",
        auth_version=0,
        created_at=now,
        updated_at=now,
    )


@pytest.fixture
def api(tmp_path):
    path = tmp_path / "api.db"
    sync_engine = create_engine(f"sqlite:///{path}")
    event.listen(sync_engine, "connect", enable_foreign_keys)
    Base.metadata.create_all(sync_engine)
    admin = make_user("admin")
    with Session(sync_engine, expire_on_commit=False) as session:
        session.add(admin)
        session.commit()
        session.expunge(admin)

    # NullPool：TestClient 与测试中的 worker 各自运行事件循环，连接不能跨循环复用
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}", poolclass=NullPool)
    event.listen(engine.sync_engine, "connect", enable_foreign_keys)
    sessions = create_session_factory(engine)
    storage = MemoryStorage()
    vectors = MemoryVectorStore()
    embedding = FakeEmbedding()
    # auto_approve 控制测试 worker 是否在解析完成后自动创建索引任务
    options = SimpleNamespace(auto_approve=False)

    async def database():
        async with sessions() as session:
            yield session

    async def identity():
        return admin

    previous = app.dependency_overrides.copy()
    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_storage] = lambda: storage
    app.dependency_overrides[get_vector_store] = lambda: vectors
    app.dependency_overrides[require_current_user] = identity
    client = TestClient(app, raise_server_exceptions=False)

    def run_worker():
        async def main():
            worker_engine = create_async_engine(
                f"sqlite+aiosqlite:///{path}", poolclass=NullPool
            )
            event.listen(worker_engine.sync_engine, "connect", enable_foreign_keys)
            worker = Worker(
                create_session_factory(worker_engine),
                storage,
                [
                    ParseHandler(
                        ParsingSettings(_env_file=None),
                        auto_approve=options.auto_approve,
                    ),
                    IndexHandler(embedding, vectors),
                ],
                heartbeat_seconds=0.05,
            )
            try:
                while await worker.run_once():
                    pass
            finally:
                await worker_engine.dispose()

        asyncio.run(main())

    def db():
        return Session(sync_engine, expire_on_commit=False)

    try:
        yield SimpleNamespace(
            client=client,
            storage=storage,
            vectors=vectors,
            embedding=embedding,
            options=options,
            admin=admin,
            run_worker=run_worker,
            db=db,
        )
    finally:
        client.close()
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)
        asyncio.run(engine.dispose())
        sync_engine.dispose()


def upload(api, name="办法.txt", content=REGULATION, **fields):
    data = {"title": "金融机构管理办法", **NO_OCR, **fields}
    data = {key: value for key, value in data.items() if value is not None}
    return api.client.post(f"{API}/imports", files={"file": (name, content)}, data=data)


def imported(api, **kwargs) -> dict:
    response = upload(api, **kwargs)
    assert response.status_code == 202, response.text
    return response.json()


ENDPOINTS = [
    ("GET", "/documents"),
    ("GET", f"/documents/{uuid4()}"),
    ("PATCH", f"/documents/{uuid4()}"),
    ("GET", f"/documents/{uuid4()}/versions"),
    ("POST", "/imports"),
    ("GET", "/imports"),
    ("GET", f"/imports/{uuid4()}"),
    ("POST", f"/imports/{uuid4()}/retry"),
    ("GET", f"/versions/{uuid4()}"),
    ("PATCH", f"/versions/{uuid4()}"),
    ("GET", f"/versions/{uuid4()}/chunks"),
    ("GET", f"/versions/{uuid4()}/file"),
    ("POST", f"/versions/{uuid4()}/reparse"),
    ("POST", f"/versions/{uuid4()}/approve"),
    ("POST", f"/versions/{uuid4()}/publish"),
    ("POST", f"/versions/{uuid4()}/withdraw"),
    ("DELETE", f"/versions/{uuid4()}"),
]


@pytest.mark.parametrize(("method", "path"), ENDPOINTS)
def test_document_management_requires_an_administrator(method, path):
    client = TestClient(app, raise_server_exceptions=False)
    previous = app.dependency_overrides.copy()
    try:
        assert client.request(method, API + path).status_code == 401

        async def regular_user():
            return make_user("user")

        app.dependency_overrides[require_current_user] = regular_user
        response = client.request(method, API + path)
        assert (response.status_code, response.json()["code"]) == (
            403,
            "ADMIN_REQUIRED",
        )
    finally:
        client.close()
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)


def test_import_stores_the_original_and_queues_a_parse_job(api):
    job = imported(
        api,
        issuer="国务院",
        document_number="国务院令第523号",
        version_note="2023年第二次修订",
        published_date="2023-07-20",
        validity_status="not_yet_effective",
        source_url="https://example.com/rules/523",
        expected_articles="3",
        chunker="article",
    )

    assert (job["kind"], job["status"], job["attempts"]) == ("parse", "queued", 0)
    assert (job["file_name"], job["document_title"]) == ("办法.txt", "金融机构管理办法")
    assert job["created_by"] == str(api.admin.id)
    key = original_key(job["document_id"], job["version_id"], "txt")
    assert api.storage.objects == {key: REGULATION}
    assert api.storage.content_types[key] == "text/plain"

    with api.db() as session:
        document = session.get(Document, UUID(job["document_id"]))
        version = session.get(DocumentVersion, UUID(job["version_id"]))
    assert (document.title, document.issuer) == ("金融机构管理办法", "国务院")
    assert version.status == "uploaded"
    assert version.object_key == key
    assert (version.size_bytes, version.file_type) == (len(REGULATION), "txt")
    assert version.published_date.isoformat() == "2023-07-20"
    assert version.validity_status == "not_yet_effective"
    assert version.expected_articles == 3
    assert version.parse_options == {
        "ocr": "none",
        "chunker": "article",
        "max_chunk_chars": 800,
        "chunk_overlap": 100,
    }


def test_blank_optional_form_fields_are_treated_as_missing(api):
    job = imported(api, version_note="", published_date="", source_url="", issuer="")
    version = api.client.get(f"{API}/versions/{job['version_id']}").json()
    assert (version["version_note"], version["published_date"]) == (None, None)
    document = api.client.get(f"{API}/documents/{job['document_id']}").json()
    assert document["issuer"] is None


@pytest.mark.parametrize(
    ("fields", "name", "content", "status", "code"),
    [
        ({"title": None}, "办法.txt", REGULATION, 422, "REQUEST_VALIDATION_ERROR"),
        (
            {"source_url": "ftp://example.com"},
            "办法.txt",
            REGULATION,
            422,
            "REQUEST_VALIDATION_ERROR",
        ),
        (
            {"max_chunk_chars": "200", "chunk_overlap": "200"},
            "办法.txt",
            REGULATION,
            422,
            "REQUEST_VALIDATION_ERROR",
        ),
        (
            {"validity_status": "valid"},
            "办法.txt",
            REGULATION,
            422,
            "REQUEST_VALIDATION_ERROR",
        ),
        ({}, "setup.exe", b"MZ", 415, "DOCUMENT_UNSUPPORTED_TYPE"),
        ({}, "空.txt", b"", 422, "DOCUMENT_INVALID"),
    ],
)
def test_invalid_imports_are_rejected_without_side_effects(
    api, fields, name, content, status, code
):
    response = upload(api, name=name, content=content, **fields)
    assert (response.status_code, response.json()["code"]) == (status, code)
    assert api.storage.objects == {}
    with api.db() as session:
        assert session.scalars(select(Document)).all() == []


def test_oversized_upload_is_rejected(api, monkeypatch):
    monkeypatch.setattr(document_imports, "MAX_UPLOAD_BYTES", 10)
    response = upload(api)
    assert (response.status_code, response.json()["code"]) == (
        413,
        "DOCUMENT_TOO_LARGE",
    )
    assert api.storage.objects == {}


def test_new_version_of_an_existing_document(api):
    first = imported(api)
    document_id = first["document_id"]

    for fields, code in (
        ({"document_id": document_id, "title": "改名"}, "REQUEST_VALIDATION_ERROR"),
        ({"document_id": str(uuid4()), "title": None}, "DOCUMENT_NOT_FOUND"),
        ({"document_id": document_id, "title": None}, "DOCUMENT_VERSION_DUPLICATE"),
    ):
        assert upload(api, **fields).json()["code"] == code
    assert len(api.storage.objects) == 1

    second = imported(
        api, document_id=document_id, title=None, content=REGULATION + b"\n"
    )
    assert second["document_id"] == document_id
    versions = api.client.get(f"{API}/documents/{document_id}/versions").json()
    assert [v["id"] for v in versions] == [second["version_id"], first["version_id"]]
    assert versions[0]["latest_job"]["id"] == second["id"]


def test_concurrent_duplicate_is_reported_and_its_upload_removed(api, monkeypatch):
    first = imported(api)

    async def no_duplicate(self, document_id, sha256):
        return None

    monkeypatch.setattr(DocumentVersionRepository, "get_by_sha256", no_duplicate)
    response = upload(api, document_id=first["document_id"], title=None)
    assert (response.status_code, response.json()["code"]) == (
        409,
        "DOCUMENT_VERSION_DUPLICATE",
    )
    assert list(api.storage.objects) == [
        original_key(first["document_id"], first["version_id"], "txt")
    ]


def test_storage_outage_fails_the_import_cleanly(api):
    api.storage.unavailable = True
    response = upload(api)
    assert (response.status_code, response.json()["code"]) == (
        503,
        "STORAGE_UNAVAILABLE",
    )
    with api.db() as session:
        assert session.scalars(select(DocumentVersion)).all() == []


def test_parsed_version_exposes_chunks_and_files(api):
    job = imported(api)
    api.run_worker()

    detail = api.client.get(f"{API}/imports/{job['id']}").json()
    assert (detail["status"], detail["error_code"]) == ("succeeded", None)
    version = api.client.get(f"{API}/versions/{job['version_id']}")
    assert version.headers["Cache-Control"] == "no-store"
    version = version.json()
    assert (version["status"], version["parse_status"]) == ("parsed", "completed")
    assert (version["chunk_count"], version["has_pdf"]) == (3, False)
    assert version["parse_stats"]["chunker"] == "article"
    assert version["latest_job"]["status"] == "succeeded"
    assert version["created_at"].endswith("Z") or version["created_at"].endswith(
        "+00:00"
    )

    page = api.client.get(
        f"{API}/versions/{job['version_id']}/chunks", params={"page": 2, "page_size": 2}
    ).json()
    assert (page["total"], page["page"], page["page_size"]) == (3, 2, 2)
    assert [(c["ordinal"], c["article"]) for c in page["items"]] == [(3, "第三条")]

    original = api.client.get(f"{API}/versions/{job['version_id']}/file")
    assert original.status_code == 200
    assert original.content == REGULATION
    assert original.headers["Content-Type"] == "text/plain"
    assert original.headers["Content-Length"] == str(len(REGULATION))
    assert original.headers["Content-Disposition"].startswith("attachment;")
    assert (
        "filename*=UTF-8''%E5%8A%9E%E6%B3%95.txt"
        in original.headers["Content-Disposition"]
    )
    assert original.headers["X-Content-Type-Options"] == "nosniff"

    pdf = api.client.get(
        f"{API}/versions/{job['version_id']}/file", params={"variant": "pdf"}
    )
    assert (pdf.status_code, pdf.json()["code"]) == (404, "FILE_NOT_AVAILABLE")


def test_pdf_original_is_shown_inline(api, tmp_path):
    source = make_pdf(tmp_path / "条例.pdf", [["第一条 内容。", "第二条 内容。"]])
    job = imported(api, name="条例.pdf", content=source.read_bytes())
    api.run_worker()

    response = api.client.get(
        f"{API}/versions/{job['version_id']}/file", params={"variant": "pdf"}
    )
    assert response.content == source.read_bytes()
    assert response.headers["Content-Type"] == "application/pdf"
    assert response.headers["Content-Disposition"].startswith(
        'inline; filename="__.pdf"'
    )
    assert api.client.get(f"{API}/versions/{job['version_id']}").json()["has_pdf"]


def test_missing_versions_and_jobs_return_404(api):
    for path, code in (
        (f"/versions/{uuid4()}", "VERSION_NOT_FOUND"),
        (f"/versions/{uuid4()}/chunks", "VERSION_NOT_FOUND"),
        (f"/versions/{uuid4()}/file", "VERSION_NOT_FOUND"),
        (f"/imports/{uuid4()}", "IMPORT_JOB_NOT_FOUND"),
        (f"/documents/{uuid4()}", "DOCUMENT_NOT_FOUND"),
        (f"/documents/{uuid4()}/versions", "DOCUMENT_NOT_FOUND"),
    ):
        response = api.client.get(API + path)
        assert (response.status_code, response.json()["code"]) == (404, code)


def test_missing_original_object_is_reported(api):
    job = imported(api)
    api.storage.objects.clear()
    response = api.client.get(f"{API}/versions/{job['version_id']}/file")
    assert (response.status_code, response.json()["code"]) == (
        422,
        "DOCUMENT_FILE_MISSING",
    )


def test_failed_job_can_be_retried_once_the_cause_is_fixed(api):
    job = imported(api, name="损坏.pdf", content=b"not really a pdf")
    api.run_worker()
    failed = api.client.get(f"{API}/imports/{job['id']}").json()
    assert (failed["status"], failed["error_code"]) == ("failed", "DOCUMENT_INVALID")

    retried = api.client.post(f"{API}/imports/{job['id']}/retry")
    assert retried.status_code == 202
    body = retried.json()
    assert (body["id"], body["status"], body["attempts"], body["error_code"]) == (
        job["id"],
        "queued",
        0,
        None,
    )
    version = api.client.get(f"{API}/versions/{job['version_id']}").json()
    assert version["status"] == "uploaded"

    again = api.client.post(f"{API}/imports/{job['id']}/retry")
    assert (again.status_code, again.json()["code"]) == (
        409,
        "IMPORT_JOB_NOT_RETRYABLE",
    )
    missing = api.client.post(f"{API}/imports/{uuid4()}/retry")
    assert missing.json()["code"] == "IMPORT_JOB_NOT_FOUND"


def test_retry_requires_the_version_to_still_be_failed(api):
    job = imported(api, name="损坏.pdf", content=b"not really a pdf")
    api.run_worker()
    with api.db() as session:
        session.get(DocumentVersion, UUID(job["version_id"])).status = "parsed"
        session.commit()
    response = api.client.post(f"{API}/imports/{job['id']}/retry")
    assert (response.status_code, response.json()["code"]) == (
        409,
        "VERSION_STATE_CONFLICT",
    )


def test_reparse_with_new_options_replaces_chunks(api):
    job = imported(api)
    api.run_worker()
    version_url = f"{API}/versions/{job['version_id']}"

    invalid = api.client.post(
        f"{version_url}/reparse", json={"max_chunk_chars": 200, "chunk_overlap": 200}
    )
    assert invalid.status_code == 422
    options = {
        "ocr": "none",
        "chunker": "length",
        "max_chunk_chars": 4000,
        "chunk_overlap": 0,
    }
    response = api.client.post(f"{version_url}/reparse", json=options)
    assert response.status_code == 202
    second = response.json()
    assert (second["kind"], second["status"]) == ("parse", "queued")
    assert second["id"] != job["id"]
    busy = api.client.post(f"{version_url}/reparse")
    assert (busy.status_code, busy.json()["code"]) == (409, "IMPORT_IN_PROGRESS")

    api.run_worker()
    version = api.client.get(version_url).json()
    assert version["parse_options"] == options
    assert (version["status"], version["chunk_count"]) == ("parsed", 1)
    assert version["parse_stats"]["chunker"] == "length"

    # 不提交参数时沿用上次的参数
    assert api.client.post(f"{version_url}/reparse").status_code == 202
    api.run_worker()
    assert api.client.get(version_url).json()["parse_options"] == options


def test_reparse_rejects_versions_outside_review(api):
    job = imported(api)
    api.run_worker()
    with api.db() as session:
        session.get(DocumentVersion, UUID(job["version_id"])).status = "published"
        session.commit()
    response = api.client.post(f"{API}/versions/{job['version_id']}/reparse")
    assert (response.status_code, response.json()["code"]) == (
        409,
        "VERSION_STATE_CONFLICT",
    )
    assert api.client.post(f"{API}/versions/{uuid4()}/reparse").status_code == 404


def test_deleting_a_draft_removes_records_and_objects(api):
    job = imported(api)
    busy = api.client.delete(f"{API}/versions/{job['version_id']}")
    assert (busy.status_code, busy.json()["code"]) == (409, "IMPORT_IN_PROGRESS")
    api.run_worker()
    assert blocks_key(job["document_id"], job["version_id"]) in api.storage.objects

    response = api.client.delete(f"{API}/versions/{job['version_id']}")
    assert response.status_code == 204
    assert api.storage.objects == {}
    assert api.client.get(f"{API}/versions/{job['version_id']}").status_code == 404
    assert api.client.get(f"{API}/imports/{job['id']}").status_code == 404
    with api.db() as session:
        assert session.scalars(select(DocumentChunk)).all() == []
        assert session.scalars(select(ImportJob)).all() == []
    document = api.client.get(f"{API}/documents/{job['document_id']}").json()
    assert (document["version_count"], document["latest_version"]) == (0, None)


def test_published_versions_cannot_be_deleted(api):
    job = imported(api)
    api.run_worker()
    with api.db() as session:
        session.get(DocumentVersion, UUID(job["version_id"])).status = "published"
        session.commit()
    response = api.client.delete(f"{API}/versions/{job['version_id']}")
    assert (response.status_code, response.json()["code"]) == (
        409,
        "VERSION_STATE_CONFLICT",
    )
    assert api.client.delete(f"{API}/versions/{uuid4()}").status_code == 404


def test_storage_outage_after_deletion_leaves_only_orphaned_objects(api):
    job = imported(api)
    api.run_worker()
    api.storage.unavailable = True
    assert api.client.delete(f"{API}/versions/{job['version_id']}").status_code == 204
    api.storage.unavailable = False
    assert api.client.get(f"{API}/versions/{job['version_id']}").status_code == 404
    assert api.storage.objects


def test_document_list_searches_filters_and_summarizes_versions(api):
    first = imported(api, title="证券公司风险处置条例")
    second = imported(api, title="非银行支付机构监督管理条例", content=b"x")
    third = imported(api, title="50%_比例规则", content=b"y")
    with api.db() as session:
        session.get(DocumentVersion, UUID(first["version_id"])).status = "published"
        session.commit()

    def titles(**params):
        body = api.client.get(f"{API}/documents", params=params).json()
        return [item["title"] for item in body["items"]], body["total"]

    assert titles(q="条例") == (
        ["非银行支付机构监督管理条例", "证券公司风险处置条例"],
        2,
    )
    assert titles(q="%_") == (["50%_比例规则"], 1)
    assert titles(q="0%") == (["50%_比例规则"], 1)
    assert titles(published="true") == (["证券公司风险处置条例"], 1)
    assert titles(published="false")[1] == 2
    page = api.client.get(f"{API}/documents", params={"page": 2, "page_size": 2}).json()
    assert (page["total"], [i["title"] for i in page["items"]]) == (
        3,
        ["证券公司风险处置条例"],
    )

    listed = {
        item["id"]: item for item in api.client.get(f"{API}/documents").json()["items"]
    }
    published = listed[first["document_id"]]
    assert published["version_count"] == 1
    assert published["published_version"]["id"] == first["version_id"]
    assert listed[second["document_id"]]["published_version"] is None
    assert listed[third["document_id"]]["latest_version"]["status"] == "uploaded"


def test_document_details_can_be_edited(api):
    job = imported(api, issuer="国务院")
    url = f"{API}/documents/{job['document_id']}"

    updated = api.client.patch(url, json={"title": "  新标题  "}).json()
    assert (updated["title"], updated["issuer"]) == ("新标题", "国务院")
    cleared = api.client.patch(url, json={"issuer": None}).json()
    assert cleared["issuer"] is None
    for body in ({}, {"title": None}, {"title": ""}, {"external_id": "x"}):
        assert api.client.patch(url, json=body).status_code == 422
    missing = api.client.patch(f"{API}/documents/{uuid4()}", json={"title": "x"})
    assert missing.json()["code"] == "DOCUMENT_NOT_FOUND"


def test_version_metadata_can_be_corrected_without_reparsing(api):
    job = imported(api, version_note="初版")
    url = f"{API}/versions/{job['version_id']}"

    response = api.client.patch(
        url,
        json={
            "validity_status": "effective",
            "validity_checked_at": "2026-11-05",
            "source_url": "https://example.com/rules",
            "version_note": None,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert (body["validity_status"], body["validity_checked_at"]) == (
        "effective",
        "2026-11-05",
    )
    assert (body["source_url"], body["version_note"]) == (
        "https://example.com/rules",
        None,
    )
    assert body["latest_job"]["status"] == "queued"
    for invalid in (
        {},
        {"validity_status": None},
        {"source_url": "javascript:alert(1)"},
        {"expected_articles": 0},
        {"file_name": "x.pdf"},
    ):
        assert api.client.patch(url, json=invalid).status_code == 422
    assert (
        api.client.patch(
            f"{API}/versions/{uuid4()}", json={"version_note": "x"}
        ).json()["code"]
        == "VERSION_NOT_FOUND"
    )


def test_job_list_filters_and_pages(api):
    first = imported(api, title="甲")
    second = imported(api, title="乙", content=b"z")
    api.client.post(f"{API}/imports/{first['id']}/retry")

    def ids(**params):
        body = api.client.get(f"{API}/imports", params=params).json()
        return [item["id"] for item in body["items"]], body["total"]

    assert ids() == ([second["id"], first["id"]], 2)
    assert ids(document_id=first["document_id"]) == ([first["id"]], 1)
    assert ids(version_id=second["version_id"]) == ([second["id"]], 1)
    assert ids(status="queued", kind="parse")[1] == 2
    assert ids(status="failed") == ([], 0)
    assert ids(page=2, page_size=1) == ([first["id"]], 2)
    assert (
        api.client.get(f"{API}/imports", params={"status": "done"}).status_code == 422
    )


def test_file_names_and_download_headers():
    assert clean_file_name("C:\\Users\\me\\制度.pdf") == "制度.pdf"
    assert clean_file_name("../../etc/passwd") == "passwd"
    long_name = "长" * 300 + ".docx"
    cleaned = clean_file_name(long_name)
    assert len(cleaned) == 255 and cleaned.endswith(".docx")
    assert clean_file_name(None) == ""

    assert media_type_for("pdf") == "application/pdf"
    assert media_type_for("doc") == "application/msword"
    assert media_type_for("md") == "text/markdown"
    assert media_type_for("unknownext") == "application/octet-stream"
    assert content_disposition('a"b.pdf', "application/pdf") == (
        "inline; filename=\"a_b.pdf\"; filename*=UTF-8''a%22b.pdf"
    )
    assert content_disposition("制度.docx", media_type_for("docx")).startswith(
        'attachment; filename="__.docx"'
    )


def test_openapi_describes_the_import_form():
    schema = app.openapi()
    body = schema["paths"]["/api/v1/admin/imports"]["post"]["requestBody"]
    assert list(body["content"]) == ["multipart/form-data"]
    form = schema["components"]["schemas"]["ImportForm"]
    assert form["properties"]["file"]["format"] == "binary"
    assert json.dumps(form["required"]) == '["file"]'


def ready_version(api, **kwargs) -> dict:
    """导入并自动批准，返回进入 ready 的版本的导入任务。"""
    api.options.auto_approve = True
    job = imported(api, **kwargs)
    api.run_worker()
    version = api.client.get(f"{API}/versions/{job['version_id']}").json()
    assert version["status"] == "ready", version
    return job


def test_auto_approved_versions_are_indexed_and_wait_for_publication(api):
    job = ready_version(api)
    version = api.client.get(f"{API}/versions/{job['version_id']}").json()
    assert version["index_model"] == "fake-embedding:4"
    assert version["indexed_at"] is not None
    assert version["latest_job"]["kind"] == "index"
    assert version["latest_job"]["status"] == "succeeded"
    stored = api.vectors.by_version(UUID(job["version_id"]))
    assert len(stored) == version["chunk_count"] == 3
    assert stored[0].text.startswith("金融机构管理办法 第一章 总则 第一条\n")
    # 每批不超过向量化服务的上限
    assert [len(batch) for batch in api.embedding.batches] == [2, 1]
    jobs = api.client.get(
        f"{API}/imports", params={"version_id": job["version_id"]}
    ).json()
    assert [item["kind"] for item in jobs["items"]] == ["index", "parse"]


def test_manual_approval_indexes_a_parsed_version(api):
    job = imported(api)
    api.run_worker()
    url = f"{API}/versions/{job['version_id']}"
    assert api.client.get(url).json()["status"] == "parsed"

    approved = api.client.post(f"{url}/approve")
    assert approved.status_code == 202
    assert (approved.json()["kind"], approved.json()["status"]) == ("index", "queued")
    assert api.client.get(url).json()["status"] == "indexing"
    busy = api.client.post(f"{url}/approve")
    assert (busy.status_code, busy.json()["code"]) == (409, "IMPORT_IN_PROGRESS")

    api.run_worker()
    assert api.client.get(url).json()["status"] == "ready"
    again = api.client.post(f"{url}/approve")
    assert (again.status_code, again.json()["code"]) == (409, "VERSION_STATE_CONFLICT")


def test_publishing_replaces_the_current_version(api):
    first = ready_version(api)
    first_url = f"{API}/versions/{first['version_id']}"
    early = api.client.post(f"{API}/versions/{uuid4()}/publish")
    assert early.json()["code"] == "VERSION_NOT_FOUND"

    published = api.client.post(f"{first_url}/publish")
    assert published.status_code == 200
    body = published.json()
    assert body["status"] == "published"
    assert body["published_by"] == str(api.admin.id)
    assert body["published_at"] is not None

    second = ready_version(
        api, document_id=first["document_id"], title=None, content=REGULATION + b"\n"
    )
    assert (
        api.client.post(f"{API}/versions/{second['version_id']}/publish").json()[
            "status"
        ]
        == "published"
    )
    assert api.client.get(first_url).json()["status"] == "archived"
    # 被替换的版本不再参与检索，其向量已清理；新版本的向量保留
    assert api.vectors.by_version(UUID(first["version_id"])) == []
    assert len(api.vectors.by_version(UUID(second["version_id"]))) == 3

    document = api.client.get(f"{API}/documents/{first['document_id']}").json()
    assert document["published_version"]["id"] == second["version_id"]
    again = api.client.post(f"{first_url}/publish")
    assert (again.status_code, again.json()["code"]) == (409, "VERSION_STATE_CONFLICT")


def test_only_ready_versions_can_be_published(api):
    job = imported(api)
    response = api.client.post(f"{API}/versions/{job['version_id']}/publish")
    assert (response.status_code, response.json()["code"]) == (
        409,
        "IMPORT_IN_PROGRESS",
    )
    api.run_worker()
    response = api.client.post(f"{API}/versions/{job['version_id']}/publish")
    assert (response.status_code, response.json()["code"]) == (
        409,
        "VERSION_STATE_CONFLICT",
    )


def test_withdrawn_versions_can_be_approved_and_published_again(api):
    job = ready_version(api)
    url = f"{API}/versions/{job['version_id']}"
    not_published = api.client.post(f"{url}/withdraw")
    assert not_published.json()["code"] == "VERSION_STATE_CONFLICT"
    api.client.post(f"{url}/publish")

    withdrawn = api.client.post(f"{url}/withdraw")
    assert withdrawn.status_code == 200
    assert withdrawn.json()["status"] == "archived"
    assert api.vectors.by_version(UUID(job["version_id"])) == []
    document = api.client.get(f"{API}/documents/{job['document_id']}").json()
    assert document["published_version"] is None
    # 归档版本保留切片，不能删除
    assert api.client.delete(url).json()["code"] == "VERSION_STATE_CONFLICT"

    assert api.client.post(f"{url}/approve").status_code == 202
    api.run_worker()
    assert len(api.vectors.by_version(UUID(job["version_id"]))) == 3
    assert api.client.post(f"{url}/publish").json()["status"] == "published"


@pytest.mark.parametrize("action", ["withdraw", "replace"])
def test_delayed_archive_cleanup_preserves_republished_vectors(
    api, monkeypatch, action
):
    first = ready_version(api)
    version_id = UUID(first["version_id"])
    url = f"{API}/versions/{version_id}"
    assert api.client.post(f"{url}/publish").status_code == 200
    if action == "replace":
        second = ready_version(
            api,
            document_id=first["document_id"],
            title=None,
            content=REGULATION + b"\n",
        )
        action_url = f"{API}/versions/{second['version_id']}/publish"
    else:
        action_url = f"{url}/withdraw"

    entered = threading.Event()
    release = threading.Event()
    cleanup = document_publishing._delete_archived_vectors

    async def delayed_cleanup(identifier, session, store):
        if identifier == version_id and not entered.is_set():
            entered.set()
            assert await asyncio.to_thread(release.wait, 5)
        await cleanup(identifier, session, store)

    monkeypatch.setattr(
        document_publishing, "_delete_archived_vectors", delayed_cleanup
    )
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(api.client.post, action_url)
        try:
            assert entered.wait(5)
            assert api.client.post(f"{url}/approve").status_code == 202
            api.run_worker()
            assert api.client.post(f"{url}/publish").status_code == 200
        finally:
            release.set()
        assert pending.result(timeout=5).status_code == 200
    assert api.client.get(url).json()["status"] == "published"
    assert len(api.vectors.by_version(version_id)) == 3
    api.client.app.dependency_overrides[get_retriever] = lambda: HybridRetriever(
        api.embedding, api.vectors
    )
    result = api.client.post(
        "/api/v1/search",
        json={"query": "金融机构", "document_ids": [first["document_id"]]},
    )
    assert result.status_code == 200 and len(result.json()["results"]) == 3


def test_ready_versions_can_be_reparsed_or_deleted_with_their_vectors(api):
    job = ready_version(api)
    url = f"{API}/versions/{job['version_id']}"
    assert api.client.post(f"{url}/reparse").status_code == 202
    api.run_worker()
    assert api.client.get(url).json()["status"] == "ready"

    assert api.client.delete(url).status_code == 204
    assert api.vectors.records == {}
    assert api.storage.objects == {}


def test_vector_store_outage_does_not_block_withdrawal_or_deletion(api):
    job = ready_version(api)
    url = f"{API}/versions/{job['version_id']}"
    api.client.post(f"{url}/publish")
    api.vectors.unavailable = True
    assert api.client.post(f"{url}/withdraw").json()["status"] == "archived"
    api.vectors.unavailable = False
    # 清理失败只留下孤儿向量；检索只读已发布版本，不受影响
    assert len(api.vectors.by_version(UUID(job["version_id"]))) == 3


def test_failed_indexing_can_be_retried(api):
    api.options.auto_approve = True
    api.embedding.fail_with = ValueError("embedding bug")
    job = imported(api)
    for _ in range(3):
        api.run_worker()
        with api.db() as session:
            for record in session.scalars(select(ImportJob)):
                record.run_after = datetime(2000, 1, 1)
            session.commit()
    jobs = api.client.get(
        f"{API}/imports", params={"version_id": job["version_id"], "kind": "index"}
    ).json()["items"]
    assert (jobs[0]["status"], jobs[0]["error_code"]) == (
        "failed",
        "JOB_INTERNAL_ERROR",
    )
    url = f"{API}/versions/{job['version_id']}"
    assert api.client.get(url).json()["status"] == "failed"

    api.embedding.fail_with = None
    retried = api.client.post(f"{API}/imports/{jobs[0]['id']}/retry")
    assert retried.status_code == 202
    assert api.client.get(url).json()["status"] == "indexing"
    api.run_worker()
    assert api.client.get(url).json()["status"] == "ready"
