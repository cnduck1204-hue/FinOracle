"""视觉实现的兼容入口，具体职责分别归入解析、索引与检索模块。"""

from app.rag.indexing.page_store import PageHit, PageRecord, PageVectorStore
from app.rag.indexing.visual_embedding import MultiVector, VisualEmbeddingProvider
from app.rag.indexing.visual_index import VisualIndexer
from app.rag.parsing.pages import page_count, render_pages
from app.rag.retrieval.visual import (
    VisualHybridRetriever,
    VisualOnlyRetriever,
    visual_channel,
)

__all__ = [
    "MultiVector",
    "PageHit",
    "PageRecord",
    "PageVectorStore",
    "VisualEmbeddingProvider",
    "VisualHybridRetriever",
    "VisualIndexer",
    "VisualOnlyRetriever",
    "page_count",
    "render_pages",
    "visual_channel",
]
