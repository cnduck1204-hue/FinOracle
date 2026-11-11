import io

import pytest
from PIL import Image
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from app.core.exceptions import AppException
from app.rag.parsing.options import ParseOptions
from app.rag.parsing.parsers.pdf import PdfParser
from tests.parsing_samples import make_pdf, text_image


def test_text_pages_keep_page_numbers_and_reading_order(tmp_path):
    path = make_pdf(
        tmp_path / "rules.pdf",
        [
            ["第一章 总则", "第一条 为了规范金融机构经营行为，制定本办法。"],
            ["第二条 本办法适用于境内设立的金融机构。"],
        ],
    )

    document = PdfParser().parse(path, ParseOptions())

    assert document.page_count == 2
    assert document.ocr_targets == []
    assert [(block.page, block.order, block.kind) for block in document.blocks] == [
        (1, 0, "paragraph"),
        (2, 1, "paragraph"),
    ]
    assert document.blocks[0].text.splitlines() == [
        "第一章 总则",
        "第一条 为了规范金融机构经营行为，制定本办法。",
    ]


def test_tables_are_extracted_once_and_in_position(tmp_path):
    path = make_pdf(
        tmp_path / "table.pdf",
        [
            [
                "第三条 资本要求如下，以下表格为准。",
                ("table", [["项目", "标准"], ["资本充足率", "8%"]]),
                "第四条 未达标准的，应当限期整改。",
            ]
        ],
    )

    blocks = PdfParser().parse(path, ParseOptions()).blocks

    assert [block.kind for block in blocks] == ["paragraph", "table", "paragraph"]
    assert blocks[1].text == "项目 | 标准\n资本充足率 | 8%"
    assert "资本充足率" not in blocks[0].text + blocks[2].text


def test_scanned_page_becomes_whole_page_ocr_target(tmp_path):
    path = make_pdf(
        tmp_path / "scan.pdf",
        [
            ["第一条 为了规范金融机构经营行为，制定本办法。"],
            [("image", text_image(), 450)],
        ],
    )

    document = PdfParser().parse(path, ParseOptions(render_dpi=72))

    assert len(document.blocks) == 1
    [target] = document.ocr_targets
    assert (target.page, target.order, target.reason) == (2, 1, "scanned_page")
    assert target.image.startswith(b"\x89PNG")


def test_large_images_on_text_pages_are_cropped_and_small_ones_ignored(tmp_path):
    path = make_pdf(
        tmp_path / "mixed.pdf",
        [
            [
                "第五条 监管机构可以采取下列措施，具体见下图。",
                # 宽 500pt 的插图约占页面 11%，超过默认 10% 的 OCR 阈值
                ("image", text_image(), 500),
                ("image", Image.new("RGB", (40, 40), "red"), 20),
                "第六条 本办法由监管机构负责解释。",
            ]
        ],
    )

    document = PdfParser().parse(path, ParseOptions())

    [target] = document.ocr_targets
    assert (target.page, target.order, target.reason) == (1, 1, "image_region")
    assert [block.order for block in document.blocks] == [0, 2]


def test_page_limit_and_broken_files_are_rejected(tmp_path):
    path = make_pdf(
        tmp_path / "long.pdf", [["第一页内容足够长，用于测试页数上限。"]] * 3
    )
    with pytest.raises(AppException) as too_long:
        PdfParser().parse(path, ParseOptions(max_pages=2))
    assert (too_long.value.code, too_long.value.status_code) == (
        "DOCUMENT_TOO_MANY_PAGES",
        413,
    )

    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"%PDF-1.7\nnot really a pdf")
    with pytest.raises(AppException) as invalid:
        PdfParser().parse(broken, ParseOptions())
    assert (invalid.value.code, invalid.value.status_code) == ("DOCUMENT_INVALID", 422)


def test_encrypted_pdf_is_rejected(tmp_path):
    path = tmp_path / "locked.pdf"
    pdf = canvas.Canvas(str(path), encrypt="secret")
    pdf.drawString(72, 700, "confidential")
    pdf.save()
    with pytest.raises(AppException) as exc:
        PdfParser().parse(path, ParseOptions())
    assert exc.value.code == "DOCUMENT_INVALID"


def test_blank_and_short_text_pages_are_read_without_ocr(tmp_path):
    path = make_pdf(tmp_path / "short.pdf", [[], ["附件"]])

    document = PdfParser().parse(path, ParseOptions())

    assert document.ocr_targets == []
    assert [(block.page, block.text) for block in document.blocks] == [(2, "附件")]


def test_images_under_an_existing_text_layer_are_not_ocred(tmp_path):
    path = tmp_path / "searchable.pdf"
    pdf = canvas.Canvas(str(path))
    pdf.drawImage(ImageReader(text_image()), 72, 500, width=500, height=120)
    pdf.drawString(80, 550, "Capital adequacy requirement text layer")
    pdf.showPage()
    pdf.save()

    document = PdfParser().parse(path, ParseOptions())

    assert document.ocr_targets == []
    assert document.blocks[0].text == "Capital adequacy requirement text layer"


def test_render_size_is_capped_for_huge_pages(tmp_path):
    path = tmp_path / "poster.pdf"
    pdf = canvas.Canvas(str(path), pagesize=(7200, 7200))
    pdf.drawImage(ImageReader(text_image()), 0, 0, width=7200, height=1600)
    pdf.showPage()
    pdf.save()

    document = PdfParser().parse(path, ParseOptions(max_render_side=1000))

    image = Image.open(io.BytesIO(document.ocr_targets[0].image))
    assert max(image.size) <= 1000
