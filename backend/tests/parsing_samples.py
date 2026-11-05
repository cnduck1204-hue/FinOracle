"""生成解析测试用的 PDF / Word / 图片样本。"""

import io
from pathlib import Path

from docx import Document
from docx.shared import Inches
from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen import canvas

from app.rag.parsing.converters import ConverterProvider

FONT = "STSong-Light"
# Word 97-2003 (.doc) 的 OLE2 复合文档文件头
OLE2_HEADER = bytes.fromhex("d0cf11e0a1b11ae1")
pdfmetrics.registerFont(UnicodeCIDFont(FONT))


def text_image(text: str = "Article 4 capital", size=(1000, 220)) -> Image.Image:
    image = Image.new("RGB", size, "white")
    font = ImageFont.load_default(size=52)
    ImageDraw.Draw(image).text((20, 70), text, fill="black", font=font)
    return image


def png_bytes(image: Image.Image) -> bytes:
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def make_pdf(path: Path, pages: list[list]) -> Path:
    """每页若干项：字符串为一行文字，("image", 图片, 宽度) 为插图，("table", 行) 为带框表格。"""
    pdf = canvas.Canvas(str(path))
    for items in pages:
        y = 780.0
        for item in items:
            if isinstance(item, str):
                pdf.setFont(FONT, 12)
                pdf.drawString(72, y, item)
                y -= 22
            elif item[0] == "image":
                _, image, width = item
                height = width * image.height / image.width
                y -= height
                pdf.drawImage(ImageReader(image), 72, y, width=width, height=height)
                y -= 16
            else:
                rows = item[1]
                xs = [72 + 120 * index for index in range(len(rows[0]) + 1)]
                ys = [y - 24 * index for index in range(len(rows) + 1)]
                pdf.grid(xs, ys)
                pdf.setFont(FONT, 12)
                for row_index, row in enumerate(rows):
                    for column, cell in enumerate(row):
                        pdf.drawString(xs[column] + 6, ys[row_index] - 17, cell)
                y = ys[-1] - 22
        pdf.showPage()
    pdf.save()
    return path


def make_docx(path: Path, *, picture: Image.Image | None = None) -> Path:
    document = Document()
    document.add_heading("金融机构管理办法", level=0)
    document.add_heading("第一章 总则", level=1)
    document.add_paragraph("第一条 为了规范金融机构经营行为，制定本办法。")
    table = document.add_table(rows=2, cols=3)
    merged = table.cell(0, 0).merge(table.cell(0, 1))
    merged.text = "项目"
    table.cell(0, 2).text = "标准"
    for column, text in enumerate(("资本", "充足率", "8%")):
        table.cell(1, column).text = text
    document.add_paragraph("")
    if picture is not None:
        run = document.add_paragraph().add_run()
        run.add_picture(io.BytesIO(png_bytes(picture)), width=Inches(4))
        icon = Image.new("RGB", (20, 20), "red")
        run.add_picture(io.BytesIO(png_bytes(icon)), width=Inches(0.2))
    document.add_paragraph("第二条 本办法自公布之日起施行。")
    document.save(str(path))
    return path


class FakeConverter(ConverterProvider):
    """把任意输入“转换”为预设内容的 PDF，并记录调用。"""

    name = "fake"

    def __init__(self, pages: list[list]):
        self.pages = pages
        self.calls: list[Path] = []

    def to_pdf(self, path: Path, workdir: Path) -> Path:
        self.calls.append(path)
        return make_pdf(workdir / f"{path.stem}.pdf", self.pages)
