"""文本索引器的兼容入口。"""

from app.rag.indexing.text_index import Entries, TextIndexer, plain_entries

__all__ = ["Entries", "TextIndexer", "plain_entries"]
