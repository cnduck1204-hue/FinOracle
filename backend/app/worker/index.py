"""索引任务：调用默认方案的索引器写入向量，完成后版本进入 ready（待发布）。

失败时版本置为 failed，可在任务列表中重试。
"""

from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from app.rag.indexing import EmbeddingProvider, VectorStore
from app.rag.indexing.text_index import TextIndexer
from app.rag.pipelines.base import IndexTarget, StoredUnit, UnitIndexer
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


class IndexHandler:
    kind = "index"

    def __init__(
        self,
        embedding: EmbeddingProvider,
        store: VectorStore,
        *,
        indexers: Sequence[UnitIndexer] | None = None,
    ) -> None:
        self.embedding = embedding
        self.indexers = tuple(indexers or (TextIndexer(embedding, store),))

    async def run(self, context: JobContext) -> None:
        target, units = await self._load(context)
        for indexer in self.indexers:
            await indexer.index(context, target, units)
        await self._save(context)

    async def on_failure(self, session: AsyncSession, context: JobContext) -> None:
        version = await DocumentVersionRepository(session).get(
            context.job.version_id, for_update=True
        )
        # 版本已被重新解析或改变状态时保持原状
        if version is not None and version.status == "indexing":
            version.status = "failed"

    async def _load(self, context: JobContext) -> tuple[IndexTarget, list[StoredUnit]]:
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
            target = IndexTarget(
                document_id=version.document_id,
                version_id=version.id,
                title=document.title,
            )
            return target, [StoredUnit.from_row(chunk) for chunk in chunks]

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
