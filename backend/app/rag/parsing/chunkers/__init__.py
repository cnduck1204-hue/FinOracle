"""切片器：按名称创建，auto 时按文档体例选择。

新增条文体例：继承 StructuredChunker 并实现 match_label，再加入 STRUCTURED_CHUNKERS，
auto 按其顺序检测。其他切法：继承 ChunkerProvider 并实现 split，再在 create_chunker
中按名称创建。
"""

from app.rag.parsing.chunkers.base import ChunkerProvider
from app.rag.parsing.chunkers.length import LengthChunker
from app.rag.parsing.chunkers.structured import (
    ArticleChunker,
    ItemChunker,
    StructuredChunker,
)
from app.rag.parsing.models import TextBlock
from app.rag.parsing.options import AUTO, ParseOptions

STRUCTURED_CHUNKERS: dict[str, type[StructuredChunker]] = {
    ArticleChunker.name: ArticleChunker,
    ItemChunker.name: ItemChunker,
}
CHUNKER_CHOICES = (AUTO, *STRUCTURED_CHUNKERS, LengthChunker.name)
# auto 至少识别出这么多个递增标签才按该体例切片
MIN_LABELS = 2


def create_chunker(
    name: str, blocks: list[TextBlock], options: ParseOptions
) -> ChunkerProvider:
    if name in STRUCTURED_CHUNKERS:
        return STRUCTURED_CHUNKERS[name](options.max_chunk_chars)
    if name == AUTO:
        for chunker_class in STRUCTURED_CHUNKERS.values():
            chunker = chunker_class(options.max_chunk_chars)
            if chunker.count_labels(blocks) >= MIN_LABELS:
                return chunker
    elif name != LengthChunker.name:
        raise ValueError(f"未知的切片器：{name}")
    return LengthChunker(options.max_chunk_chars, options.chunk_overlap)


__all__ = [
    "CHUNKER_CHOICES",
    "STRUCTURED_CHUNKERS",
    "ArticleChunker",
    "ChunkerProvider",
    "ItemChunker",
    "LengthChunker",
    "StructuredChunker",
    "create_chunker",
]
