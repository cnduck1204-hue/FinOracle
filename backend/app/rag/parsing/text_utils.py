"""清洗与切片共用的文本工具：中文编号数字与空白处理。"""

import re

# 章、节、条编号中的数字，如“十二”“一百零三”“15”
CHINESE_NUMERAL = r"[零〇一二三四五六七八九十百千两\d]+"
_DIGITS = dict(zip("零〇一二两三四五六七八九", (0, 0, 1, 2, 2, 3, 4, 5, 6, 7, 8, 9)))
_UNITS = {"十": 10, "百": 100, "千": 1000}
_WHITESPACE = re.compile(r"\s+")


def chinese_number(text: str) -> int:
    if text.isdigit():
        return int(text)
    total = current = 0
    for char in text:
        if char in _UNITS:
            total += (current or 1) * _UNITS[char]
            current = 0
        else:
            current = _DIGITS.get(char, 0)
    return total + current


def compact(text: str) -> str:
    """去掉全部空白，用于标签规范化和忽略排版差异的文本比较。"""
    return _WHITESPACE.sub("", text)
