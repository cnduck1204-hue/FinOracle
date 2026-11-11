"""入库与检索共用的数据结构和任务操作协议。"""

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol
from uuid import UUID

from app.rag.parsing.models import Chunk
from app.storage import ObjectStorage

# 给定 (版本, 页码)，返回这些页上的 (切片 ID, 版本, 页码)
PageUnits = Callable[
    [Sequence[tuple[UUID, int]]], Awaitable[list[tuple[int, UUID, int]]]
]


class IngestRun(Protocol):
    """入库过程可用的任务操作，由 worker 的 JobContext 提供。"""

    storage: ObjectStorage

    def report(
        self, stage: str, current: int = 0, total: int | None = None
    ) -> None: ...

    async def run_owned(
        self, operation: Callable[..., None], /, *args: object, **kwargs: object
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class SourceFile:
    """待解析的版本文件。"""

    document_id: UUID
    version_id: UUID
    object_key: str
    file_type: str
    parse_options: dict[str, Any]
    # .doc 等格式转换后的 PDF
    pdf_object_key: str | None = None

    @property
    def pdf_key(self) -> str | None:
        """可按页处理的 PDF；没有时为 None。"""
        return self.pdf_object_key or (
            self.object_key if self.file_type == "pdf" else None
        )


@dataclass(slots=True)
class ParsedUnits:
    """解析器的结果，切片尚未写入数据库。"""

    chunks: list[Chunk]
    status: str
    page_count: int | None
    stats: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    parser_version: str | None = None
    # 解析过程中新生成的 PDF
    pdf_object_key: str | None = None


@dataclass(frozen=True, slots=True)
class StoredUnit:
    """已写入数据库的切片；脱离数据库会话后供索引器使用。"""

    id: int
    chapter: str
    section: str
    article: str
    heading: str
    content: str
    page_start: int | None

    @classmethod
    def from_row(cls, row: Any) -> "StoredUnit":
        return cls(
            id=row.id,
            chapter=row.chapter,
            section=row.section,
            article=row.article,
            heading=row.heading,
            content=row.content,
            page_start=row.page_start,
        )


@dataclass(frozen=True, slots=True)
class IndexTarget:
    document_id: UUID
    version_id: UUID
    title: str
    pdf_key: str | None = None
