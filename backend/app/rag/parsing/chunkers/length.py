"""通用切片：按标题分段，段内按长度切分并保留重叠。"""

from app.rag.parsing.chunkers.base import (
    ChunkerProvider,
    Line,
    append_chunks,
    group_lines,
    to_lines,
)
from app.rag.parsing.models import Chunk, TextBlock
from app.rag.parsing.options import DEFAULT_CHUNK_OVERLAP, DEFAULT_MAX_CHUNK_CHARS


class LengthChunker(ChunkerProvider):
    name = "length"

    def __init__(
        self,
        max_chars: int = DEFAULT_MAX_CHUNK_CHARS,
        overlap: int = DEFAULT_CHUNK_OVERLAP,
    ):
        self.max_chars = max_chars
        self.overlap = overlap

    def split(self, blocks: list[TextBlock]) -> list[Chunk]:
        chunks: list[Chunk] = []
        headings: dict[int, str] = {}
        current: list[Line] = []

        def flush() -> None:
            if current:
                groups = group_lines(current, self.max_chars, self.overlap)
                heading = " / ".join(headings[level] for level in sorted(headings))
                append_chunks(chunks, groups, heading=heading)
                current.clear()

        for line in to_lines(blocks):
            if line.level is None:
                current.append(line)
                continue
            flush()
            # 新标题替换同级及更低级的标题，形成“总则 / 细则”式路径
            for level in [level for level in headings if level >= line.level]:
                del headings[level]
            headings[line.level] = line.text
        flush()
        return chunks
