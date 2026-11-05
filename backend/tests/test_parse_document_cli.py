import json

from app.core.config import get_parsing_settings
from app.schemas.document import ParseRequest
from app.services import document_parsing
from scripts import parse_document


def test_cli_writes_json_and_summary(tmp_path, capsys):
    source = tmp_path / "rules.txt"
    source.write_text("第一条 甲。\n第二条 乙。\n", encoding="utf-8")
    output = tmp_path / "result.json"

    code = parse_document.main([str(source), "--ocr", "none", "--output", str(output)])

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert code == 0
    assert payload["blocks"] is None
    assert [chunk["article"] for chunk in payload["chunks"]] == ["第一条", "第二条"]
    assert capsys.readouterr().err.startswith("completed: 2 chunks via article")


def test_cli_prints_blocks_to_stdout(tmp_path, capsysbinary):
    source = tmp_path / "notes.md"
    source.write_text("# 标题\n正文\n", encoding="utf-8")

    code = parse_document.main([str(source), "--ocr", "none", "--blocks"])

    payload = json.loads(capsysbinary.readouterr().out.decode("utf-8"))
    assert code == 0
    assert [block["kind"] for block in payload["blocks"]] == ["heading", "paragraph"]


def test_cli_forwards_options_to_service(tmp_path, monkeypatch):
    calls = []
    real_parse = document_parsing.parse_document

    def spy(path, request, settings):
        calls.append((request, settings.soffice_path))
        return real_parse(path, request, settings)

    monkeypatch.setattr(document_parsing, "parse_document", spy)
    source = tmp_path / "notes.txt"
    source.write_text("正文", encoding="utf-8")
    arguments = ["--ocr", "none", "--chunker", "length", "--max-chars", "300"]
    arguments += ["--overlap", "20", "--soffice", "soffice"]

    code = parse_document.main(
        [str(source), *arguments, "--output", str(tmp_path / "out.json")]
    )

    assert code == 0
    assert calls == [
        (
            ParseRequest(
                ocr="none", chunker="length", max_chunk_chars=300, chunk_overlap=20
            ),
            "soffice",
        )
    ]


def test_cli_rejects_invalid_options_and_files(tmp_path, capsys):
    source = tmp_path / "notes.txt"
    source.write_text("正文", encoding="utf-8")
    assert (
        parse_document.main([str(source), "--max-chars", "200", "--overlap", "300"])
        == 2
    )
    assert "Invalid option" in capsys.readouterr().err

    assert parse_document.main([str(tmp_path / "missing.pdf")]) == 2
    assert "File not found" in capsys.readouterr().err

    unsupported = tmp_path / "sheet.xlsx"
    unsupported.write_bytes(b"data")
    assert parse_document.main([str(unsupported), "--ocr", "none"]) == 1
    assert capsys.readouterr().err.startswith("DOCUMENT_UNSUPPORTED_TYPE")


def test_cli_reads_conversion_settings_from_environment(tmp_path, monkeypatch):
    calls = []

    def spy(path, request, settings):
        calls.append((settings.gotenberg_url, settings.soffice_path))
        return real_parse(path, request, settings)

    real_parse = document_parsing.parse_document
    monkeypatch.setattr(document_parsing, "parse_document", spy)
    monkeypatch.setenv("GOTENBERG_URL", "http://127.0.0.1:3000")
    monkeypatch.setenv("SOFFICE_PATH", "D:/LibreOffice/program/soffice.exe")
    get_parsing_settings.cache_clear()
    source = tmp_path / "notes.txt"
    source.write_text("正文", encoding="utf-8")

    try:
        code = parse_document.main(
            [str(source), "--ocr", "none", "--output", str(tmp_path / "out.json")]
        )
    finally:
        get_parsing_settings.cache_clear()

    assert code == 0
    assert calls == [("http://127.0.0.1:3000", "D:/LibreOffice/program/soffice.exe")]
