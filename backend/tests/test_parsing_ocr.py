import sys
from types import SimpleNamespace

import pytest

import app.rag.parsing.ocr as ocr_package
from app.core.exceptions import AppException
from app.rag.parsing.ocr import (
    OCR_PROVIDERS,
    OcrLine,
    OCRProvider,
    create_ocr,
    rapid_ocr,
)
from app.rag.parsing.ocr.rapid_ocr import RapidOcrProvider
from tests.parsing_samples import make_pdf, text_image


class StubOcr(OCRProvider):
    name = "stub"
    lines: list[OcrLine] = []

    def read_lines(self, image):
        return self.lines


class MissingOcr(StubOcr):
    name = "missing"

    @classmethod
    def available(cls):
        return False


def test_base_drops_blank_and_low_confidence_lines():
    provider = StubOcr(min_confidence=0.5)
    provider.lines = [OcrLine("第一条", 0.98), OcrLine(" ", 0.9), OcrLine("噪声", 0.3)]
    assert provider.recognize(b"png") == [OcrLine("第一条", 0.98)]


def test_rapidocr_adapts_engine_output():
    calls = []

    def engine(image):
        calls.append(image)
        return SimpleNamespace(txts=("第一条", "噪声"), scores=(0.98, 0.3))

    provider = RapidOcrProvider(engine=engine)

    assert provider.read_lines(b"png") == [
        OcrLine("第一条", 0.98),
        OcrLine("噪声", 0.3),
    ]
    assert provider.recognize(b"png") == [OcrLine("第一条", 0.98)]
    assert calls == [b"png", b"png"]
    empty = RapidOcrProvider(
        engine=lambda image: SimpleNamespace(txts=None, scores=None)
    )
    assert empty.recognize(b"png") == []


def test_rapidocr_reports_missing_dependency(monkeypatch):
    monkeypatch.setattr(rapid_ocr, "find_spec", lambda name: None)
    assert RapidOcrProvider.available() is False

    monkeypatch.setitem(sys.modules, "rapidocr", None)
    with pytest.raises(AppException) as exc:
        RapidOcrProvider()
    assert exc.value.code == "OCR_UNAVAILABLE"


def test_create_ocr_picks_by_name_or_first_available(monkeypatch):
    monkeypatch.setattr(
        ocr_package, "OCR_PROVIDERS", {"missing": MissingOcr, "stub": StubOcr}
    )

    assert create_ocr("none") is None
    auto = create_ocr("auto", min_confidence=0.8)
    assert isinstance(auto, StubOcr)
    assert auto.min_confidence == 0.8
    # 显式指定时由实现自身报告依赖缺失
    assert isinstance(create_ocr("missing"), MissingOcr)
    with pytest.raises(ValueError):
        create_ocr("tesseract")

    monkeypatch.setattr(ocr_package, "OCR_PROVIDERS", {"missing": MissingOcr})
    assert create_ocr("auto") is None


def test_registered_providers_match_their_names():
    for name, provider in OCR_PROVIDERS.items():
        assert issubclass(provider, OCRProvider)
        assert provider.name == name


def test_rapidocr_reads_rendered_scan(tmp_path):
    pytest.importorskip("rapidocr")
    from app.rag.parsing import parse_file

    path = make_pdf(tmp_path / "scan.pdf", [[("image", text_image(), 450)]])
    result = parse_file(path, ocr=RapidOcrProvider())

    assert result.status == "completed"
    assert result.chunks[0].content == "Article 4 capital"
    assert result.chunks[0].sources == ["ocr"]
