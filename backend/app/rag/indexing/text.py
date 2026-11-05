"""向量化文本：标题与条文位置加正文。修改规则等同于更换模型，已入库的版本需要重建索引。"""

from typing import Protocol

# text-embedding-v4 单条上限 8192 token；中文约 1–1.5 字一个 token，6000 字留有余量。
# 解析切片不超过 4000 字，只有外部导入的超长条文会被截断
MAX_INDEX_TEXT_CHARS = 6000


class ChunkFields(Protocol):
    chapter: str
    section: str
    article: str
    heading: str
    content: str


def index_text(title: str, chunk: ChunkFields) -> str:
    """制度标题 + 章 + 节 + 条号（无条号时为标题路径）+ 正文。"""
    labels = (title, chunk.chapter, chunk.section, chunk.article or chunk.heading)
    header = " ".join(label for label in labels if label)
    text = f"{header}\n{chunk.content}" if header else chunk.content
    return text[:MAX_INDEX_TEXT_CHARS]
