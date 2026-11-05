import asyncio
import hashlib
import io

import pytest
from fastapi import UploadFile
from pydantic import ValidationError

from app.core.config import ParsingSettings
from app.core.exceptions import AppException
from app.rag.parsing.options import MIB
from app.schemas.document import ParseRequest
from app.services import document_parsing

NO_OCR = ParseRequest(ocr="none")
# 不读取本机 .env，避免本地转换配置影响测试
SETTINGS = ParsingSettings(_env_file=None)


@pytest.fixture(autouse=True)
def fresh_ocr_cache():
    document_parsing._ocr_provider.cache_clear()
    yield
    document_parsing._ocr_provider.cache_clear()


def upload(name: str, data: bytes, size: int | None = None) -> UploadFile:
    return UploadFile(
        io.BytesIO(data), filename=name, size=len(data) if size is None else size
    )


def test_request_defaults_and_validation():
    defaults = ParseRequest()
    assert defaults.model_dump() == {
        "ocr": "auto",
        "chunker": "auto",
        "max_chunk_chars": 800,
        "chunk_overlap": 100,
        "include_blocks": False,
    }
    invalid = [
        {"ocr": "unknown-ocr"},
        {"chunker": "semantic"},
        {"max_chunk_chars": 50},
        {"max_chunk_chars": 200, "chunk_overlap": 200},
        {"unknown": 1},
    ]
    for fields in invalid:
        with pytest.raises(ValidationError):
            ParseRequest(**fields)


def test_parse_document_applies_request_options(tmp_path):
    path = tmp_path / "notes.md"
    path.write_text("# 总则\n" + "第一段内容。" * 40 + "\n", encoding="utf-8")

    result = document_parsing.parse_document(
        path,
        ParseRequest(
            ocr="none", chunker="length", max_chunk_chars=100, chunk_overlap=0
        ),
    )
    with_blocks = document_parsing.parse_document(
        path, ParseRequest(ocr="none", include_blocks=True)
    )

    assert result.chunker == "length"
    assert len(result.chunks) > 1
    assert all(len(chunk.content) <= 100 for chunk in result.chunks)
    assert result.blocks is None
    assert [block.kind for block in with_blocks.blocks] == ["heading", "paragraph"]


def test_ocr_provider_is_created_once_per_name(tmp_path, monkeypatch):
    created = []

    def fake_create(name):
        created.append(name)

    monkeypatch.setattr(document_parsing, "create_ocr", fake_create)
    path = tmp_path / "notes.txt"
    path.write_text("正文", encoding="utf-8")
    for _ in range(2):
        document_parsing.parse_document(path, ParseRequest())

    assert created == ["auto"]


def test_parse_upload_keeps_original_name_and_removes_temp_file(monkeypatch):
    parsed_paths = []
    real_parse = document_parsing._parse

    def spy(path, request, settings):
        # 接口解析在锁内执行
        assert document_parsing._PARSE_LOCK.locked()
        parsed_paths.append(path)
        return real_parse(path, request, settings)

    monkeypatch.setattr(document_parsing, "_parse", spy)
    data = "第一条 甲。\n第二条 乙。\n".encode()

    result = asyncio.run(
        document_parsing.parse_upload(upload("../规则.TXT", data), NO_OCR, SETTINGS)
    )

    assert result.file_name == "规则.TXT"
    assert result.file_type == "txt"
    assert result.sha256 == hashlib.sha256(data).hexdigest()
    assert [chunk.article for chunk in result.chunks] == ["第一条", "第二条"]
    assert parsed_paths[0].suffix == ".txt"
    assert not parsed_paths[0].exists()


def test_parse_upload_rejects_type_and_size_before_copying(monkeypatch):
    saved = []
    monkeypatch.setattr(document_parsing, "_save", lambda *args: saved.append(args))
    cases = [
        (upload("sheet.xlsx", b"x"), "DOCUMENT_UNSUPPORTED_TYPE"),
        (upload("README", b"x"), "DOCUMENT_UNSUPPORTED_TYPE"),
        (upload("large.pdf", b"x", size=60 * MIB), "DOCUMENT_TOO_LARGE"),
    ]
    for file, code in cases:
        with pytest.raises(AppException) as exc:
            asyncio.run(document_parsing.parse_upload(file, NO_OCR, SETTINGS))
        assert exc.value.code == code
    assert saved == []
