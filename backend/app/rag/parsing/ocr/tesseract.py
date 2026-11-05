"""Tesseract：调用系统安装的 tesseract 命令，无需 Python 依赖，Docker 镜像内置。"""

import csv
import io
import re
import shutil
import subprocess

from app.rag.parsing.errors import ocr_unavailable
from app.rag.parsing.ocr.base import OcrLine, OCRProvider
from app.rag.parsing.options import DEFAULT_MIN_OCR_CONFIDENCE

_EXECUTABLE = "tesseract"
_LANGUAGES = "chi_sim+eng"
# 把图片当作单一文本块：制度文件多为单栏正文，实测比默认的自动分版漏字更少
_PAGE_SEGMENTATION = "6"
_TIMEOUT_SECONDS = 120
# TSV 中 level 5 为词，其余层级为页、块、段、行
_WORD_LEVEL = "5"
_LATIN = re.compile(r"[A-Za-z0-9]")


class TesseractOcrProvider(OCRProvider):
    name = "tesseract"

    def __init__(self, min_confidence: float = DEFAULT_MIN_OCR_CONFIDENCE):
        super().__init__(min_confidence)
        executable = shutil.which(_EXECUTABLE)
        if executable is None:
            raise ocr_unavailable("未安装 Tesseract，请安装 tesseract-ocr 及中文语言包")
        self._executable = executable

    @classmethod
    def available(cls) -> bool:
        return shutil.which(_EXECUTABLE) is not None

    def read_lines(self, image: bytes) -> list[OcrLine]:
        command = [
            self._executable,
            "stdin",
            "stdout",
            "-l",
            _LANGUAGES,
            "--psm",
            _PAGE_SEGMENTATION,
            "tsv",
        ]
        result = subprocess.run(
            command,
            input=image,
            capture_output=True,
            check=True,
            timeout=_TIMEOUT_SECONDS,
        )
        return lines_from_tsv(result.stdout.decode("utf-8"))


def lines_from_tsv(tsv: str) -> list[OcrLine]:
    """按页、块、段、行合并词，行置信度取各词置信度的均值并换算到 0–1。"""
    lines: dict[tuple[str, ...], list[tuple[str, float]]] = {}
    rows = csv.DictReader(io.StringIO(tsv), delimiter="\t", quoting=csv.QUOTE_NONE)
    for row in rows:
        text = (row.get("text") or "").strip()
        if row["level"] != _WORD_LEVEL or not text:
            continue
        key = (row["page_num"], row["block_num"], row["par_num"], row["line_num"])
        lines.setdefault(key, []).append((text, float(row["conf"]) / 100))
    return [
        OcrLine(
            _join_words([text for text, _ in words]),
            sum(confidence for _, confidence in words) / len(words),
        )
        for words in lines.values()
    ]


def _join_words(words: list[str]) -> str:
    """中文直接相连；相邻英文单词或数字之间保留空格。"""
    text = ""
    for word in words:
        if text and _LATIN.match(text[-1]) and _LATIN.match(word[0]):
            text += " "
        text += word
    return text
