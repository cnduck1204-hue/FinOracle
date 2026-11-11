"""向量化文本：标题与条文位置加正文。修改规则等同于更换模型，已入库的版本需要重建索引。"""

import re
from collections.abc import Sequence
from typing import Protocol, TypeVar

# text-embedding-v4 单条上限 8192 token；中文约 1–1.5 字一个 token，6000 字留有余量。
# 解析切片不超过 4000 字，只有外部导入的超长条文会被截断
MAX_INDEX_TEXT_CHARS = 6000
_MAX_TITLE_CHARS = 120
_MAX_TITLE_LINES = 3
_DOCUMENT_NUMBER = re.compile(r"[〔［\[(（]\s*\d{4}\s*[〕］\])）].*号$")
_SENTENCE_ENDINGS = ("。", "；", ";", "：", ":", "！", "？", "，", ",")


class ChunkFields(Protocol):
    chapter: str
    section: str
    article: str
    heading: str
    content: str


ChunkT = TypeVar("ChunkT", bound=ChunkFields)


def index_text(title: str, chunk: ChunkFields, source_title: str = "") -> str:
    """制度标题（及原文标题）+ 章 + 节 + 条号（无条号时为标题路径）+ 正文。"""
    labels = (
        *_distinct_titles(title, source_title),
        chunk.chapter,
        chunk.section,
        chunk.article or chunk.heading,
    )
    header = " ".join(label for label in labels if label)
    text = f"{header}\n{chunk.content}" if header else chunk.content
    return text[:MAX_INDEX_TEXT_CHARS]


def index_entries(title: str, chunks: Sequence[ChunkT]) -> list[tuple[ChunkT, str]]:
    """按序生成需要入库的切片及其向量化文本。

    条文正文常常不含制度主题，主题只写在公文标题里，所以把原文标题带到每一条。
    标题已带到各条后，只有标题的切片不再单独入库，避免占据结果却没有条文内容。
    """
    source_title, title_only = _source_title(chunks)
    if not source_title:
        return [(chunk, index_text(title, chunk)) for chunk in chunks]
    # 第一个切片的正文已含标题，不重复加入
    entries = [] if title_only else [(chunks[0], index_text(title, chunks[0]))]
    entries += [(chunk, index_text(title, chunk, source_title)) for chunk in chunks[1:]]
    return entries


def _distinct_titles(title: str, source_title: str) -> tuple[str, ...]:
    """登记标题与原文标题互相包含时只保留较长的一个。"""
    if not source_title or source_title in title:
        return (title,)
    if title in source_title:
        return (source_title,)
    return (title, source_title)


def _source_title(chunks: Sequence[ChunkFields]) -> tuple[str, bool]:
    """从条文之前的首个切片读取公文标题，返回标题及该切片是否只有标题。

    只处理按条文切分的文档；按长度切片的文档，标题路径已在 heading 中。
    """
    if len(chunks) < 2 or not any(chunk.article for chunk in chunks[1:]):
        return "", False
    first = chunks[0]
    if first.chapter or first.section or first.article or first.heading:
        return "", False
    lines = [line.strip() for line in first.content.split("\n") if line.strip()]
    # 文号写在标题之前时跳过，但这类切片要保留，文号本身是检索入口
    has_number = bool(lines) and bool(_DOCUMENT_NUMBER.search(lines[0]))
    start = 1 if has_number else 0
    title_lines: list[str] = []
    for line in lines[start : start + _MAX_TITLE_LINES]:
        if line.endswith(_SENTENCE_ENDINGS) or _DOCUMENT_NUMBER.search(line):
            break
        if sum(map(len, title_lines)) + len(line) > _MAX_TITLE_CHARS:
            break
        title_lines.append(line)
    title_only = bool(title_lines) and not has_number and len(title_lines) == len(lines)
    return "".join(title_lines), title_only
