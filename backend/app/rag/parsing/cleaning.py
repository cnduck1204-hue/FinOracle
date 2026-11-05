"""文本汇合与清洗：合并文本层与 OCR 结果，规范字符，去掉页眉页脚和重复内容。"""

import math
import re
import unicodedata
from collections import Counter, defaultdict

from app.rag.parsing.models import TextBlock
from app.rag.parsing.text_utils import CHINESE_NUMERAL, compact

# 只对这些兼容字符做 NFKC：康熙部首、全角空格、兼容汉字、全角字母数字；中文全角标点保留
_COMPAT_RANGES = (
    (0x2F00, 0x2FDF),
    (0x3000, 0x3000),
    (0xF900, 0xFAFF),
    (0xFF10, 0xFF19),
    (0xFF21, 0xFF3A),
    (0xFF41, 0xFF5A),
)
# 汉字与中文标点；两者之间的空格多为排版产生
_CJK = r"\u3001-\u303f\u3400-\u4dbf\u4e00-\u9fff\uff01-\uffef"
_CJK_GAP = re.compile(rf"(?<=[{_CJK}]) (?=[{_CJK}])")
_SPACES = re.compile(r"[ \t\u00a0]+")
# 行首“第X章 / 条”等标签及其后的空格
_LABEL = re.compile(
    rf"(第\s*{CHINESE_NUMERAL}\s*[编章节条](?:\s*之\s*{CHINESE_NUMERAL})?) "
)
# 独占一行的页码：“- 3 -”“第 3 页 共 9 页”“3/9”“Page 3 of 9”
_PAGE_NUMBER = re.compile(
    r"[-—–_\s]*\d+[-—–_\s]*"
    r"|第\s*\d+\s*页(?:\s*[/／,，]?\s*共\s*\d+\s*页)?"
    r"|\d+\s*/\s*\d+"
    r"|page\s*\d+(?:\s*of\s*\d+)?",
    re.IGNORECASE,
)
# 页眉页脚可能有两层（如标题行 + 页码），每页首尾最多剥离两次
_MAX_FURNITURE_LAYERS = 2
# 至少 3 页且在过半页面首尾出现的同一行（忽略数字差异）视为页眉页脚
_MIN_PAGES_FOR_REPEATS = 3
_REPEAT_RATIO = 0.5

Position = tuple[int, int]


def normalize_text(text: str) -> str:
    text = "".join(
        unicodedata.normalize("NFKC", char) if _is_compat(char) else char
        for char in text
    )
    lines = (_normalize_line(line) for line in text.splitlines())
    return "\n".join(line for line in lines if line)


def merge_blocks(
    blocks: list[TextBlock], ocr_blocks: list[TextBlock]
) -> tuple[list[TextBlock], int]:
    """按阅读顺序合并文本层与 OCR 块，返回清洗后的块和删除的行数。"""
    cleaned = _normalized(blocks)
    page_text: dict[int, str] = defaultdict(str)
    for block in cleaned:
        if block.page is not None:
            page_text[block.page] += compact(block.text)

    removed = 0
    for block in _normalized(ocr_blocks):
        # 文本层已包含的 OCR 结果（如带文字的插图）不重复收录
        if block.page is not None and compact(block.text) in page_text[block.page]:
            removed += _line_count(block.text)
            continue
        cleaned.append(block)
    cleaned.sort(key=lambda block: block.order)

    cleaned, furniture = _strip_page_furniture(cleaned)
    removed += furniture

    merged: list[TextBlock] = []
    for block in cleaned:
        if merged and merged[-1].text == block.text and merged[-1].kind == block.kind:
            removed += _line_count(block.text)
            continue
        merged.append(block)
    return merged, removed


def _normalize_line(line: str) -> str:
    line = _SPACES.sub(" ", line).strip()
    # 去掉汉字间的排版空格，保留“第一条”等标签与正文之间的分隔
    label = _LABEL.match(line)
    if label:
        return f"{compact(label.group(1))} {_CJK_GAP.sub('', line[label.end() :])}"
    return _CJK_GAP.sub("", line)


def _normalized(blocks: list[TextBlock]) -> list[TextBlock]:
    result = []
    for block in blocks:
        text = normalize_text(block.text)
        if text:
            result.append(block.model_copy(update={"text": text}))
    return result


def _strip_page_furniture(blocks: list[TextBlock]) -> tuple[list[TextBlock], int]:
    """删除各页首尾的页码，以及多数页面首尾重复出现的页眉页脚行。"""
    pages: dict[int, list[int]] = defaultdict(list)
    for index, block in enumerate(blocks):
        if block.page is not None:
            pages[block.page].append(index)
    if not pages:
        return blocks, 0

    lines = [block.text.split("\n") for block in blocks]
    threshold = (
        math.ceil(len(pages) * _REPEAT_RATIO)
        if len(pages) >= _MIN_PAGES_FOR_REPEATS
        else None
    )
    removed = 0
    for _ in range(_MAX_FURNITURE_LAYERS):
        edges = [_page_edges(indexes, lines) for indexes in pages.values()]
        counts = Counter(
            key
            for positions in edges
            for key in {_furniture_key(lines[i][j]) for i, j in positions}
        )
        doomed = {
            (i, j)
            for positions in edges
            for i, j in positions
            if _is_furniture(lines[i][j], counts, threshold, len(positions) == 1)
        }
        if not doomed:
            break
        for i, j in sorted(doomed, reverse=True):
            del lines[i][j]
        removed += len(doomed)

    result = [
        block.model_copy(update={"text": "\n".join(text)})
        for block, text in zip(blocks, lines, strict=True)
        if text
    ]
    return result, removed


def _page_edges(indexes: list[int], lines: list[list[str]]) -> set[Position]:
    """一页剩余内容的首行与末行位置（块下标，行下标）。"""
    filled = [index for index in indexes if lines[index]]
    if not filled:
        return set()
    first, last = filled[0], filled[-1]
    return {(first, 0), (last, len(lines[last]) - 1)}


def _is_furniture(
    line: str, counts: Counter[str], threshold: int | None, sole_line: bool
) -> bool:
    if _PAGE_NUMBER.fullmatch(line):
        return True
    # 页面只剩一行时视为正文，不按重复行删除
    if threshold is None or sole_line:
        return False
    return counts[_furniture_key(line)] >= threshold


def _furniture_key(line: str) -> str:
    return re.sub(r"\d+", "#", line)


def _line_count(text: str) -> int:
    return text.count("\n") + 1


def _is_compat(char: str) -> bool:
    code = ord(char)
    return any(start <= code <= end for start, end in _COMPAT_RANGES)
