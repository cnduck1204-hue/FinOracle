"""制度类切片：章、节作为元数据，每个条文标签开始一个片段；子类定义标签格式。"""

import re
from abc import abstractmethod

from app.rag.parsing.chunkers.base import (
    ChunkerProvider,
    Line,
    append_chunks,
    group_lines,
    to_lines,
)
from app.rag.parsing.models import Chunk, TextBlock
from app.rag.parsing.options import DEFAULT_MAX_CHUNK_CHARS
from app.rag.parsing.text_utils import CHINESE_NUMERAL as _NUM
from app.rag.parsing.text_utils import chinese_number, compact

# 标签编号：（主编号, “之X”编号），如第十条之一为 (10, 1)
LabelKey = tuple[int, int]
_NO_LABEL: LabelKey = (0, 0)

# 章、节标题：编号后至多 40 个不含句读的字符
_HEADING_TAIL = r"(\s*[^。；;：:，,]{0,40})"
_CHAPTER = re.compile(rf"第\s*({_NUM})\s*章{_HEADING_TAIL}")
_SECTION = re.compile(rf"第\s*({_NUM})\s*节{_HEADING_TAIL}")
_ARTICLE = re.compile(rf"(第\s*({_NUM})\s*条(?:\s*之\s*({_NUM}))?)(.*)", re.DOTALL)
# 通知类文件的“一、二、”一级序号
_ITEM = re.compile(r"([一二三四五六七八九十百]+)、(.*)", re.DOTALL)
# 行首“第七条规定的……”“第三章所列……”是对条文的引用，不是新条或新章
_REFERENCE = re.compile(r"(?:规定|所|的|至|、|和|及|或|中|第|[，,。；;）)])")


class StructuredChunker(ChunkerProvider):
    def __init__(self, max_chars: int = DEFAULT_MAX_CHUNK_CHARS):
        self.max_chars = max_chars

    @abstractmethod
    def match_label(self, text: str) -> tuple[str, LabelKey] | None:
        """识别行首标签，返回标签文本与用于判断递增的编号。"""

    def split(self, blocks: list[TextBlock]) -> list[Chunk]:
        chunks: list[Chunk] = []
        current: list[Line] = []
        meta = {"chapter": "", "section": "", "article": ""}
        last_chapter, last_label = 0, _NO_LABEL

        def flush() -> None:
            if current:
                append_chunks(chunks, group_lines(current, self.max_chars), **meta)
                current.clear()

        for line in to_lines(blocks):
            chapter = _match_heading(_CHAPTER, line.text)
            if chapter:
                flush()
                number = chinese_number(chapter.group(1))
                # 章号回落说明进入附件或另一部制度，标签重新计数
                if number <= last_chapter:
                    last_label = _NO_LABEL
                last_chapter = number
                meta.update(chapter=line.text, section="", article="")
                continue
            if _match_heading(_SECTION, line.text):
                flush()
                meta.update(section=line.text, article="")
                continue
            label = self._next_label(line.text, last_label)
            if label:
                flush()
                meta["article"], last_label = label
            current.append(line)
        flush()
        return chunks

    def count_labels(self, blocks: list[TextBlock]) -> int:
        count, last = 0, _NO_LABEL
        for line in to_lines(blocks):
            label = self._next_label(line.text, last)
            if label:
                count += 1
                last = label[1]
        return count

    def _next_label(self, text: str, last: LabelKey) -> tuple[str, LabelKey] | None:
        # 编号必须递增，回退的编号多为正文中对前文的引用
        label = self.match_label(text)
        return label if label and label[1] > last else None


class ArticleChunker(StructuredChunker):
    """“第X条”“第X条之一”。"""

    name = "article"

    def match_label(self, text: str) -> tuple[str, LabelKey] | None:
        match = _ARTICLE.match(text)
        if not match or _is_reference(match.group(4)):
            return None
        suffix = chinese_number(match.group(3)) if match.group(3) else 0
        key = (chinese_number(match.group(2)), suffix)
        return compact(match.group(1)), key


class ItemChunker(StructuredChunker):
    """“一、二、”一级序号；“（一）”等下级序号留在所属项内。"""

    name = "item"

    def match_label(self, text: str) -> tuple[str, LabelKey] | None:
        match = _ITEM.match(text)
        if not match:
            return None
        return f"{match.group(1)}、", (chinese_number(match.group(1)), 0)


def _match_heading(pattern: re.Pattern[str], text: str) -> re.Match[str] | None:
    match = pattern.fullmatch(text)
    if match and _is_reference(match.group(2)):
        return None
    return match


def _is_reference(rest: str) -> bool:
    # 正式条文、标题与正文之间通常有空格；紧接引用词才视为对条文的引用
    return bool(rest) and not rest[0].isspace() and bool(_REFERENCE.match(rest))
