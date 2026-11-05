import hashlib

import pytest

from app.core.exceptions import AppException
from app.rag.parsing import ParseOptions, parse_file
from app.rag.parsing.ocr import OcrLine, OCRProvider
from tests.parsing_samples import OLE2_HEADER, FakeConverter, make_pdf, text_image


class FakeOcr(OCRProvider):
    name = "fake"

    def __init__(self, *responses):
        super().__init__()
        self.responses = list(responses)

    def read_lines(self, image):
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return [OcrLine(text, 0.99) for text in response]


@pytest.fixture
def scanned_pdf(tmp_path):
    return make_pdf(
        tmp_path / "scan.pdf",
        [
            ["第一章 总则", "第一条 为了规范金融机构经营行为，制定本办法。"],
            [("image", text_image(), 450)],
            [("image", text_image(), 450)],
        ],
    )


def test_pipeline_merges_ocr_pages_into_article_chunks(scanned_pdf):
    ocr = FakeOcr(
        ["第二条 本办法适用于", "境内金融机构。"], ["第三条 自公布之日起施行。"]
    )

    result = parse_file(scanned_pdf, ocr=ocr)

    assert result.status == "completed"
    assert result.chunker == "article"
    assert result.ocr_provider == "fake"
    assert result.sha256 == hashlib.sha256(scanned_pdf.read_bytes()).hexdigest()
    assert result.page_count == 3
    assert [(c.article, c.page_start, c.sources) for c in result.chunks] == [
        ("第一条", 1, ["text"]),
        ("第二条", 2, ["ocr"]),
        ("第三条", 3, ["ocr"]),
    ]
    assert result.chunks[1].content == "第二条 本办法适用于\n境内金融机构。"
    assert result.stats.model_dump() == {
        "text_blocks": 1,
        "ocr_targets": 2,
        "ocr_blocks": 2,
        "ocr_pending": 0,
        "removed_lines": 0,
        "chunks": 3,
    }


def test_pipeline_marks_pending_ocr_without_provider_or_on_failure(scanned_pdf):
    without = parse_file(scanned_pdf)
    assert without.status == "needs_ocr"
    assert without.stats.ocr_pending == 2
    assert without.warnings == ["第 2 页、第 3 页待 OCR，未配置 OCR Provider"]
    # 仅剩一条时不足以判定为条文体例，按长度切片且保留文本层内容
    assert without.chunker == "length"
    assert without.chunks[0].content.endswith("制定本办法。")

    failed = parse_file(scanned_pdf, ocr=FakeOcr(RuntimeError("engine"), []))
    assert failed.status == "needs_ocr"
    assert failed.stats.ocr_pending == 1
    assert failed.warnings == ["第 2 页OCR 失败", "第 3 页未识别出文字"]


def test_pipeline_reports_empty_documents(tmp_path):
    path = tmp_path / "blank.txt"
    path.write_text("\n\n  \n", encoding="utf-8")
    result = parse_file(path)
    assert result.status == "empty"
    assert result.chunker == "length"
    assert result.chunks == []


def test_pipeline_validates_type_and_size(tmp_path):
    cases = [
        (tmp_path / "data.xlsx", b"data", ParseOptions(), "DOCUMENT_UNSUPPORTED_TYPE"),
        (tmp_path / "README", b"data", ParseOptions(), "DOCUMENT_UNSUPPORTED_TYPE"),
        (tmp_path / "empty.txt", b"", ParseOptions(), "DOCUMENT_INVALID"),
        (
            tmp_path / "large.txt",
            b"x" * 2048,
            ParseOptions(max_file_bytes=1024),
            "DOCUMENT_TOO_LARGE",
        ),
    ]
    for path, content, options, code in cases:
        path.write_bytes(content)
        with pytest.raises(AppException) as exc:
            parse_file(path, options=options)
        assert exc.value.code == code


def test_pipeline_converts_legacy_formats_to_pdf(tmp_path):
    source = tmp_path / "legacy.doc"
    source.write_bytes(OLE2_HEADER + b"legacy word")
    converter = FakeConverter(
        [["第一章 总则", "第一条 为了规范经营，制定本办法。"], ["第二条 适用范围。"]]
    )

    result = parse_file(source, converter=converter)

    assert converter.calls == [source]
    assert (result.file_name, result.file_type, result.converter) == (
        "legacy.doc",
        "doc",
        "fake",
    )
    assert result.sha256 == hashlib.sha256(source.read_bytes()).hexdigest()
    assert result.page_count == 2
    assert [(c.article, c.page_start) for c in result.chunks] == [
        ("第一条", 1),
        ("第二条", 2),
    ]
    with pytest.raises(AppException) as exc:
        parse_file(source)
    assert exc.value.code == "DOCUMENT_CONVERSION_UNAVAILABLE"

    # 内容与扩展名不符时在转换前拒绝，不调用转换器
    fake = tmp_path / "fake.doc"
    fake.write_text("改了扩展名的纯文本", encoding="utf-8")
    with pytest.raises(AppException) as exc:
        parse_file(fake, converter=converter)
    assert exc.value.code == "DOCUMENT_INVALID"
    assert converter.calls == [source]


def test_native_formats_skip_the_converter(tmp_path):
    source = tmp_path / "notes.txt"
    source.write_text("正文", encoding="utf-8")
    converter = FakeConverter([["不应使用"]])

    result = parse_file(source, converter=converter)

    assert result.converter is None
    assert converter.calls == []


def test_pipeline_honours_chunker_choice(tmp_path):
    path = tmp_path / "rules.md"
    path.write_text("# 总则\n第一条 甲。\n第二条 乙。\n", encoding="utf-8")
    assert parse_file(path).chunker == "article"
    forced = parse_file(path, chunker="length")
    assert forced.chunker == "length"
    assert forced.chunks[0].heading == "总则"
