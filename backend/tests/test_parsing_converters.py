import codecs
import subprocess
from pathlib import Path

import httpx
import pytest

from app.core.exceptions import AppException
from app.rag.parsing.converters import (
    CONVERTIBLE_TYPES,
    GotenbergConverter,
    LibreOfficeConverter,
    check_signature,
    create_converter,
)
from tests.parsing_samples import OLE2_HEADER, make_pdf

PDF_BYTES = b"%PDF-1.7\nconverted"


@pytest.fixture
def legacy_doc(tmp_path):
    path = tmp_path / "source" / "legacy.doc"
    path.parent.mkdir()
    path.write_bytes(b"\xd0\xcf\x11\xe0 legacy word")
    return path


def gotenberg(handler) -> GotenbergConverter:
    return GotenbergConverter(
        "http://gotenberg:3000/", 30, transport=httpx.MockTransport(handler)
    )


def test_gotenberg_posts_file_and_writes_pdf(legacy_doc, tmp_path):
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, content=PDF_BYTES)

    output = gotenberg(handler).to_pdf(legacy_doc, tmp_path)

    assert output == tmp_path / "legacy.pdf"
    assert output.read_bytes() == PDF_BYTES
    [request] = requests
    body = request.read()
    assert str(request.url) == "http://gotenberg:3000/forms/libreoffice/convert"
    assert b'name="files"; filename="legacy.doc"' in body
    assert b"legacy word" in body


@pytest.mark.parametrize(
    ("response", "code"),
    [
        (httpx.Response(400, text="bad input"), "DOCUMENT_INVALID"),
        (httpx.Response(200, content=b"<html>not a pdf"), "DOCUMENT_INVALID"),
        (httpx.Response(503, text="timeout"), "DOCUMENT_CONVERSION_UNAVAILABLE"),
        (httpx.Response(500, text="oom"), "DOCUMENT_CONVERSION_UNAVAILABLE"),
    ],
)
def test_gotenberg_maps_responses_to_errors(legacy_doc, tmp_path, response, code):
    with pytest.raises(AppException) as exc:
        gotenberg(lambda request: response).to_pdf(legacy_doc, tmp_path)
    assert exc.value.code == code


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (httpx.ConnectError("refused"), "文档转换服务不可用"),
        (httpx.ReadTimeout("slow"), "文档转换服务超时"),
    ],
)
def test_gotenberg_reports_unreachable_service(legacy_doc, tmp_path, error, message):
    def handler(request):
        raise error

    with pytest.raises(AppException) as exc:
        gotenberg(handler).to_pdf(legacy_doc, tmp_path)
    assert (exc.value.code, exc.value.status_code) == (
        "DOCUMENT_CONVERSION_UNAVAILABLE",
        503,
    )
    assert exc.value.message == message


def test_libreoffice_converts_in_isolated_profile(legacy_doc, tmp_path, monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        outdir = Path(command[command.index("--outdir") + 1])
        make_pdf(outdir / "legacy.pdf", [["第一条 内容"]])
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    output = LibreOfficeConverter("soffice", 45).to_pdf(legacy_doc, tmp_path)

    [(command, kwargs)] = calls
    assert output == tmp_path / "legacy.pdf"
    assert command[0] == "soffice"
    assert command[1].startswith("-env:UserInstallation=file:")
    assert command[command.index("--convert-to") + 1] == "pdf"
    assert command[-1] == str(legacy_doc)
    assert kwargs["timeout"] == 45


def test_libreoffice_reports_missing_binary_and_failures(
    legacy_doc, tmp_path, monkeypatch
):
    monkeypatch.setattr("shutil.which", lambda name: None)
    with pytest.raises(AppException) as exc:
        LibreOfficeConverter(None, 30).to_pdf(legacy_doc, tmp_path)
    assert exc.value.code == "DOCUMENT_CONVERSION_UNAVAILABLE"

    failures = [
        (subprocess.CalledProcessError(1, "soffice"), "DOCUMENT_INVALID"),
        (subprocess.TimeoutExpired("soffice", 30), "DOCUMENT_CONVERSION_UNAVAILABLE"),
    ]
    for error, code in failures:

        def failing_run(command, error=error, **kwargs):
            raise error

        monkeypatch.setattr(subprocess, "run", failing_run)
        with pytest.raises(AppException) as exc:
            LibreOfficeConverter("soffice", 30).to_pdf(legacy_doc, tmp_path)
        assert exc.value.code == code

    # 进程成功但没有产出 PDF
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 0),
    )
    with pytest.raises(AppException) as exc:
        LibreOfficeConverter("soffice", 30).to_pdf(legacy_doc, tmp_path)
    assert exc.value.code == "DOCUMENT_INVALID"


def test_create_converter_prefers_gotenberg():
    remote = create_converter(
        gotenberg_url="http://gotenberg:3000",
        soffice_path="soffice",
        timeout_seconds=60,
    )
    local = create_converter(
        gotenberg_url=None, soffice_path="soffice", timeout_seconds=60
    )

    assert isinstance(remote, GotenbergConverter)
    assert remote.endpoint == "http://gotenberg:3000/forms/libreoffice/convert"
    assert isinstance(local, LibreOfficeConverter)
    assert (local.soffice_path, local.timeout_seconds) == ("soffice", 60)
    assert {"doc", "rtf", "odt"} <= CONVERTIBLE_TYPES
    assert "docx" not in CONVERTIBLE_TYPES


RTF_HEADER = b"{\\rtf1"
ZIP_HEADER = b"PK\x03\x04"


@pytest.mark.parametrize(
    ("file_type", "header"),
    [
        ("doc", OLE2_HEADER + b"word 97"),
        ("doc", RTF_HEADER + b" rtf saved as doc"),
        ("doc", ZIP_HEADER + b" docx renamed to doc"),
        ("doc", codecs.BOM_UTF8 + b"\r\n<HTML><body>web export</body>"),
        ("doc", b"MIME-Version: 1.0\r\nmht export"),
        ("doc", b"<?xml version='1.0'?><w:wordDocument>"),
        ("rtf", RTF_HEADER + b" document"),
        ("odt", ZIP_HEADER + b" opendocument"),
    ],
)
def test_signature_accepts_real_documents(tmp_path, file_type, header):
    path = tmp_path / f"sample.{file_type}"
    path.write_bytes(header)
    check_signature(path, file_type)


@pytest.mark.parametrize(
    ("file_type", "content"),
    [
        ("doc", "仅是改了扩展名的纯文本".encode()),
        ("doc", bytes(range(256)) * 4),
        ("rtf", OLE2_HEADER),
        ("odt", RTF_HEADER),
    ],
)
def test_signature_rejects_content_that_would_convert_as_text(
    tmp_path, file_type, content
):
    path = tmp_path / f"sample.{file_type}"
    path.write_bytes(content)
    with pytest.raises(AppException) as exc:
        check_signature(path, file_type)
    assert exc.value.code == "DOCUMENT_INVALID"
