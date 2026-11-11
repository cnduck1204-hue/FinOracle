from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import require_current_user
from app.core.config import ParsingSettings, get_parsing_settings
from app.main import app
from app.models.user import User
from app.rag.parsing import CHUNKER_CHOICES, OCR_CHOICES, providers
from app.services import document_parsing
from tests.parsing_samples import OLE2_HEADER, FakeConverter, make_pdf

URL = "/api/v1/admin/documents/parse"


def make_user(role: str) -> User:
    return User(
        id=uuid4(),
        username=f"{role}_01",
        email=f"{role}@example.com",
        role=role,
        status="active",
        password_hash="private-hash",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


@pytest.fixture
def client():
    client = TestClient(app, raise_server_exceptions=False)
    previous = app.dependency_overrides.copy()
    document_parsing.ocr_provider.cache_clear()
    try:
        yield client
    finally:
        client.close()
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)
        document_parsing.ocr_provider.cache_clear()


@pytest.fixture
def admin_client(client):
    async def identity():
        return make_user("admin")

    app.dependency_overrides[require_current_user] = identity
    return client


def test_parse_requires_admin(client):
    files = {"file": ("notes.txt", b"text")}
    assert client.post(URL, files=files).status_code == 401

    async def regular_user():
        return make_user("user")

    app.dependency_overrides[require_current_user] = regular_user
    response = client.post(URL, files=files)
    assert response.status_code == 403
    assert response.json()["code"] == "ADMIN_REQUIRED"


def test_admin_parses_uploaded_pdf(admin_client, tmp_path):
    path = make_pdf(
        tmp_path / "rules.pdf",
        [
            ["第一章 总则", "第一条 为了规范金融机构经营行为，制定本办法。"],
            ["第二条 本办法适用于境内设立的金融机构。"],
        ],
    )
    with path.open("rb") as file:
        response = admin_client.post(
            URL,
            files={"file": ("管理办法.pdf", file, "application/pdf")},
            data={"ocr": "none"},
        )

    body = response.json()
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert (body["file_name"], body["file_type"], body["status"]) == (
        "管理办法.pdf",
        "pdf",
        "completed",
    )
    assert body["chunker"] == "article"
    assert [(c["chapter"], c["article"], c["page_start"]) for c in body["chunks"]] == [
        ("第一章 总则", "第一条", 1),
        ("第一章 总则", "第二条", 2),
    ]
    assert body["blocks"] is None


def test_form_options_are_applied(admin_client):
    response = admin_client.post(
        URL,
        files={"file": ("notes.md", "# 总则\n正文".encode())},
        data={"ocr": "none", "chunker": "length", "include_blocks": "true"},
    )

    body = response.json()
    assert response.status_code == 200
    assert body["chunker"] == "length"
    assert body["chunks"][0]["heading"] == "总则"
    assert [block["kind"] for block in body["blocks"]] == ["heading", "paragraph"]


@pytest.mark.parametrize(
    ("files", "data", "status", "code"),
    [
        ({"file": ("sheet.xlsx", b"x")}, {}, 415, "DOCUMENT_UNSUPPORTED_TYPE"),
        ({"file": ("empty.txt", b"")}, {"ocr": "none"}, 422, "DOCUMENT_INVALID"),
        (
            {"file": ("a.txt", b"x")},
            {"ocr": "unknown-ocr"},
            422,
            "REQUEST_VALIDATION_ERROR",
        ),
        (
            {"file": ("a.txt", b"x")},
            {"max_chunk_chars": "200", "chunk_overlap": "300"},
            422,
            "REQUEST_VALIDATION_ERROR",
        ),
        ({"file": ("a.txt", b"x")}, {"unknown": "1"}, 422, "REQUEST_VALIDATION_ERROR"),
        ({}, {"ocr": "none"}, 422, "REQUEST_VALIDATION_ERROR"),
    ],
)
def test_invalid_uploads_are_rejected(admin_client, files, data, status, code):
    response = admin_client.post(URL, files=files or None, data=data)
    assert response.status_code == status
    assert response.json()["code"] == code


def test_doc_upload_is_converted_with_configured_converter(admin_client, monkeypatch):
    created = []
    converter = FakeConverter([["第一条 甲。", "第二条 乙。"]])

    def fake_create_converter(**settings):
        created.append(settings)
        return converter

    monkeypatch.setattr(providers, "create_converter", fake_create_converter)
    app.dependency_overrides[get_parsing_settings] = lambda: ParsingSettings(
        _env_file=None, gotenberg_url="http://gotenberg:3000"
    )

    response = admin_client.post(
        URL,
        files={"file": ("旧版制度.doc", OLE2_HEADER + b"legacy word")},
        data={"ocr": "none"},
    )

    body = response.json()
    assert response.status_code == 200
    assert (body["file_name"], body["file_type"], body["converter"]) == (
        "旧版制度.doc",
        "doc",
        "fake",
    )
    assert [chunk["article"] for chunk in body["chunks"]] == ["第一条", "第二条"]
    assert created == [
        {
            "gotenberg_url": "http://gotenberg:3000",
            "soffice_path": None,
            "timeout_seconds": 120,
        }
    ]


def test_openapi_describes_a_file_upload_form():
    spec = app.openapi()
    operation = spec["paths"][URL]["post"]
    schema_name = operation["requestBody"]["content"]["multipart/form-data"]["schema"]
    schema = spec["components"]["schemas"][schema_name["$ref"].rsplit("/", 1)[-1]]

    assert list(operation["requestBody"]["content"]) == ["multipart/form-data"]
    assert operation["security"] == [{"AccessToken": []}]
    assert schema["required"] == ["file"]
    assert schema["properties"]["file"]["format"] == "binary"
    assert schema["properties"]["ocr"]["enum"] == list(OCR_CHOICES)
    assert schema["properties"]["chunker"]["enum"] == list(CHUNKER_CHOICES)
