"""文本索引器：向量化切片并写入向量库，提供稠密向量与 BM25 关键词两路通道。"""

import asyncio
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from uuid import UUID

from app.rag.indexing import (
    EmbeddingProvider,
    VectorRecord,
    VectorStore,
    index_entries,
    index_text,
)
from app.rag.models import IndexTarget, IngestRun, StoredUnit

Entries = Callable[[str, Sequence[StoredUnit]], list[tuple[StoredUnit, str]]]


def plain_entries(
    title: str, units: Sequence[StoredUnit]
) -> list[tuple[StoredUnit, str]]:
    """每个切片都入库，文本为制度标题、标题路径加正文。"""
    return [(unit, index_text(title, unit)) for unit in units]


@dataclass(frozen=True, slots=True)
class _Item:
    chunk_id: int
    text: str


class TextIndexer:
    provides = frozenset({"dense", "keyword"})

    def __init__(
        self,
        embedding: EmbeddingProvider,
        store: VectorStore,
        *,
        entries: Entries = index_entries,
    ) -> None:
        self.embedding = embedding
        self.store = store
        self.entries = entries

    @property
    def model_id(self) -> str:
        return self.embedding.model_id

    async def index(
        self, run: IngestRun, target: IndexTarget, units: Sequence[StoredUnit]
    ) -> None:
        """先删除该版本已有的向量再分批写入，重试时结果相同。"""
        items = [
            _Item(unit.id, text) for unit, text in self.entries(target.title, units)
        ]
        total = len(items)
        run.report("indexing", 0, total)
        await asyncio.to_thread(self.store.ensure_collection)
        await run.run_owned(self.store.delete_by_version, target.version_id)
        size = self.embedding.max_batch_size
        for start in range(0, total, size):
            batch = items[start : start + size]
            vectors = await self.embedding.embed_documents(
                [item.text for item in batch]
            )
            records = [
                VectorRecord(
                    item.chunk_id,
                    target.document_id,
                    target.version_id,
                    item.text,
                    vector,
                )
                for item, vector in zip(batch, vectors, strict=True)
            ]
            await run.run_owned(self.store.upsert, records)
            run.report("embedding", start + len(batch), total)

    def delete(self, version_id: UUID) -> None:
        self.store.delete_by_version(version_id)
