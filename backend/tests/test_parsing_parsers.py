import codecs
import io

import pytest
from PIL import Image

from app.core.exceptions import AppException
from app.rag.parsing.options import ParseOptions
from app.rag.parsing.parsers import PARSER_TYPES, PARSERS, get_parser
from app.rag.parsing.parsers.base import ParserProvider, image_to_png
from app.rag.parsing.parsers.image import ImageParser
from app.rag.parsing.parsers.pdf import PdfParser
from app.rag.parsing.parsers.text import TextParser, decode_text
from app.rag.parsing.parsers.word import WordParser
from tests.parsing_samples import make_docx, text_image

OPTIONS = ParseOptions()


def test_registry_picks_parser_by_extension():
    assert isinstance(get_parser("pdf"), PdfParser)
    assert isinstance(get_parser("docx"), WordParser)
    assert isinstance(get_parser("md"), TextParser)
    assert isinstance(get_parser("tiff"), ImageParser)
    assert {"pdf", "docx", "txt", "md", "png", "jpg", "tiff"} <= PARSER_TYPES
    # .doc 需先转换为 PDF，不由解析器直接读取
    for file_type in ("doc", "xlsx", ""):
        with pytest.raises(AppException) as exc:
            get_parser(file_type)
        assert exc.value.code == "DOCUMENT_UNSUPPORTED_TYPE"


def test_registered_parsers_cover_distinct_file_types():
    file_types = [file_type for parser in PARSERS for file_type in parser.file_types]
    assert all(isinstance(parser, ParserProvider) for parser in PARSERS)
    assert len(file_types) == len(set(file_types))


def test_docx_keeps_headings_tables_and_embedded_images(tmp_path):
    path = make_docx(tmp_path / "rules.docx", picture=text_image())

    document = WordParser().parse(path, OPTIONS)

    assert document.page_count is None
    assert [(b.kind, b.level, b.text) for b in document.blocks] == [
        ("heading", 0, "金融机构管理办法"),
        ("heading", 1, "第一章 总则"),
        ("paragraph", None, "第一条 为了规范金融机构经营行为，制定本办法。"),
        ("table", None, "项目 | 标准\n资本 | 充足率 | 8%"),
        ("paragraph", None, "第二条 本办法自公布之日起施行。"),
    ]
    assert [(t.page, t.reason, t.order) for t in document.ocr_targets] == [
        (None, "embedded_image", 4)
    ]
    assert document.warnings == ["跳过 1 张过小或格式不支持的内嵌图片"]


def test_docx_rejects_corrupt_files(tmp_path):
    path = tmp_path / "broken.docx"
    path.write_bytes(b"PK\x03\x04 not a docx")
    with pytest.raises(AppException) as exc:
        WordParser().parse(path, OPTIONS)
    assert exc.value.code == "DOCUMENT_INVALID"


def test_text_and_markdown_split_paragraphs_and_headings(tmp_path):
    text = tmp_path / "notes.txt"
    text.write_text("第一条 内容\n续行\n\n\n第二条 内容\n", encoding="utf-8")
    markdown = tmp_path / "guide.md"
    markdown.write_text("# 总则 #\n说明一\n## 细则\n\n说明二\n", encoding="utf-8")

    plain = TextParser().parse(text, OPTIONS)
    marked = TextParser().parse(markdown, OPTIONS)

    assert [b.text for b in plain.blocks] == ["第一条 内容\n续行", "第二条 内容"]
    assert [(b.kind, b.level, b.text) for b in marked.blocks] == [
        ("heading", 1, "总则"),
        ("paragraph", None, "说明一"),
        ("heading", 2, "细则"),
        ("paragraph", None, "说明二"),
    ]


def test_text_decoding_accepts_utf8_bom_and_gb18030_only():
    assert decode_text(codecs.BOM_UTF8 + "金融".encode()) == "金融"
    assert decode_text("金融制度".encode("gb18030")) == "金融制度"
    for raw in (b"\x00\x01binary", b"\x81"):
        with pytest.raises(AppException) as exc:
            decode_text(raw)
        assert exc.value.code == "DOCUMENT_INVALID"


def test_image_files_become_one_ocr_page_per_frame(tmp_path):
    single = tmp_path / "scan.jpg"
    text_image().save(single)
    tiff = tmp_path / "scan.tiff"
    frames = [text_image(), text_image("Page two")]
    frames[0].save(tiff, save_all=True, append_images=frames[1:])

    one = ImageParser().parse(single, OPTIONS)
    many = ImageParser().parse(tiff, OPTIONS)

    assert one.page_count == 1
    assert [(t.page, t.order, t.reason) for t in many.ocr_targets] == [
        (1, 0, "image_file"),
        (2, 1, "image_file"),
    ]
    with pytest.raises(AppException) as exc:
        ImageParser().parse(tiff, ParseOptions(max_pages=1))
    assert exc.value.code == "DOCUMENT_TOO_MANY_PAGES"

    broken = tmp_path / "broken.png"
    broken.write_bytes(b"\x89PNG broken")
    with pytest.raises(AppException) as exc:
        ImageParser().parse(broken, OPTIONS)
    assert exc.value.code == "DOCUMENT_INVALID"


def test_image_to_png_flattens_transparency_and_drops_tiny_images():
    transparent = Image.new("RGBA", (300, 300), (0, 0, 0, 0))
    flattened = Image.open(io.BytesIO(image_to_png(transparent, 1)))
    assert flattened.mode == "RGB"
    assert flattened.getpixel((0, 0)) == (255, 255, 255)

    assert image_to_png(Image.new("RGB", (10, 10)), 1000) is None
    assert image_to_png(b"not an image", 1) is None
    assert image_to_png(Image.new("CMYK", (50, 50)), 1) is not None
