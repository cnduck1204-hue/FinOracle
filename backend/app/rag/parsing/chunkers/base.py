"""Chunker 基类与各切片器共用的分组工具。"""

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, replace

from app.rag.parsing.models import BlockSource, Chunk, TextBlock

_SENTENCE_END = re.compile(r"(?<=[。；！？;!?])")


class ChunkerProvider(ABC):
    """把清洗后的文本块切成可检索的片段。"""

    name: str

    @abstractmethod
    def split(self, blocks: list[TextBlock]) -> list[Chunk]: ...


@dataclass(frozen=True, slots=True)
class Line:
    text: str
    page: int | None
    source: BlockSource
    level: int | None = None
    # 由超长行拆出的后续片段，与前一片段直接相连而非换行
    continued: bool = False


def to_lines(blocks: list[TextBlock]) -> list[Line]:
    return [
        Line(text, block.page, block.source, block.level)
        for block in blocks
        for text in block.text.split("\n")
        if text
    ]


def group_lines(
    lines: list[Line], max_chars: int, overlap: int = 0
) -> list[list[Line]]:
    """按长度把行分组，每组不超过 max_chars 字；相邻组重叠不超过 overlap 字的尾部行。"""
    groups: list[list[Line]] = []
    current: list[Line] = []
    for unit in _split_long_lines(lines, max_chars):
        if current and _size(current + [unit]) > max_chars:
            groups.append(current)
            current = _tail(current, overlap)
            if _size(current + [unit]) > max_chars:
                current = []
        current.append(unit)
    if current:
        groups.append(current)
    return groups


def append_chunks(chunks: list[Chunk], groups: list[list[Line]], **meta: str) -> None:
    """同一段内容拆成的各组共用元数据，以 part / part_count 标记顺序。"""
    for part, group in enumerate(groups, start=1):
        pages = [line.page for line in group if line.page is not None]
        chunks.append(
            Chunk(
                ordinal=len(chunks) + 1,
                part=part,
                part_count=len(groups),
                content=_join(group),
                page_start=min(pages) if pages else None,
                page_end=max(pages) if pages else None,
                sources=sorted({line.source for line in group}),
                **meta,
            )
        )


def _split_long_lines(lines: list[Line], max_chars: int) -> list[Line]:
    """超长行先按句末标点拆开；单句仍超长时按长度硬切。"""
    units = []
    for line in lines:
        if len(line.text) <= max_chars:
            units.append(line)
            continue
        pieces: list[str] = []
        for sentence in filter(None, _SENTENCE_END.split(line.text)):
            for start in range(0, len(sentence), max_chars):
                piece = sentence[start : start + max_chars]
                if pieces and len(pieces[-1]) + len(piece) <= max_chars:
                    pieces[-1] += piece
                else:
                    pieces.append(piece)
        units.extend(
            replace(line, text=piece, continued=index > 0)
            for index, piece in enumerate(pieces)
        )
    return units


def _tail(lines: list[Line], overlap: int) -> list[Line]:
    tail: list[Line] = []
    for line in reversed(lines):
        if _size([line, *tail]) > overlap:
            break
        tail.insert(0, line)
    return tail


def _size(lines: list[Line]) -> int:
    return len(_join(lines))


def _join(lines: list[Line]) -> str:
    text = ""
    for index, line in enumerate(lines):
        separator = "" if index == 0 or line.continued else "\n"
        text += separator + line.text
    return text
