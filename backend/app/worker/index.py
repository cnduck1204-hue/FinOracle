"""索引任务：把版本的切片向量化后写入向量库，完成后版本进入 ready（待发布）。

先删除该版本已有的向量再分批写入，重试时结果相同。向量化按服务的单次上限分批，
每批报告进度。失败时版本置为 failed，可在任务列表中重试。
"""

import asyncio
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.rag.indexing import (
    EmbeddingProvider,
    VectorRecord,
    VectorStore,
    index_entries,
)
from app.repositories.documents import (
    DocumentChunkRepository,
    DocumentRepository,
    DocumentVersionRepository,
)
from app.services.document_errors import (
    document_empty,
    version_not_found,
    version_state_conflict,
)
from app.worker.runtime import JobContext


@dataclass(frozen=True, slots=True)
class _Item:
    chunk_id: int
    text: str


class IndexHandler:
    kind = "index"

    def __init__(self, embedding: EmbeddingProvider, store: VectorStore) -> None:
        self.embedding = embedding
        self.store = store

    async def run(self, context: JobContext) -> None:
        document_id, items = await self._load(context)
        version_id = context.job.version_id
        total = len(items)
        context.report("indexing", 0, total)
        await asyncio.to_thread(self.store.ensure_collection)
        await context.run_owned(self.store.delete_by_version, version_id)
        size = self.embedding.max_batch_size
        for start in range(0, total, size):
            batch = items[start : start + size]
            vectors = await self.embedding.embed_documents(
                [item.text for item in batch]
            )
            records = [
                VectorRecord(item.chunk_id, document_id, version_id, item.text, vector)
                for item, vector in zip(batch, vectors, strict=True)
            ]
            await context.run_owned(self.store.upsert, records)
            context.report("embedding", start + len(batch), total)
        await self._save(context)

    async def on_failure(self, session: AsyncSession, context: JobContext) -> None:
        version = await DocumentVersionRepository(session).get(
            context.job.version_id, for_update=True
        )
        # 版本已被重新解析或改变状态时保持原状
        if version is not None and version.status == "indexing":
            version.status = "failed"

    async def _load(self, context: JobContext) -> tuple[UUID, list[_Item]]:
        async with context.sessions() as session, session.begin():
            await context.lock_job(session)
            version = await DocumentVersionRepository(session).get(
                context.job.version_id, for_update=True
            )
            if version is None:
                raise version_not_found()
            if version.status != "indexing":
                raise version_state_conflict("版本不在待索引状态")
            document = await DocumentRepository(session).get(version.document_id)
            chunks = await DocumentChunkRepository(session).all_for_version(version.id)
            if not chunks:
                raise document_empty()
            items = [
                _Item(chunk.id, text)
                for chunk, text in index_entries(document.title, chunks)
            ]
            return version.document_id, items

    async def _save(self, context: JobContext) -> None:
        async with context.sessions() as session, session.begin():
            job = await context.lock_job(session)
            version = await DocumentVersionRepository(session).get(
                context.job.version_id, for_update=True
            )
            if version is None:
                raise version_not_found()
            version.status = "ready"
            version.index_model = self.embedding.model_id
            version.indexed_at = context.now()
            context.succeed(job)
