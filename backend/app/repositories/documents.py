"""制度、版本与切片的数据库访问。事务由服务层管理，这里只 flush，不提交。"""

from collections.abc import Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import delete, func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import (
    Document,
    DocumentChunk,
    DocumentVersion,
    content_digest,
)
from app.rag.parsing.models import Chunk


class DocumentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(self, document: Document) -> None:
        self.session.add(document)
        await self.session.flush()

    async def get(
        self, document_id: UUID, *, for_update: bool = False
    ) -> Document | None:
        return await self.session.get(
            Document, document_id, populate_existing=True, with_for_update=for_update
        )

    async def get_by_external_id(self, external_id: str) -> Document | None:
        return await self.session.scalar(
            select(Document).where(Document.external_id == external_id)
        )


class DocumentVersionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(self, version: DocumentVersion) -> None:
        self.session.add(version)
        await self.session.flush()

    async def get(
        self, version_id: UUID, *, for_update: bool = False
    ) -> DocumentVersion | None:
        """状态变更前用 for_update 锁定版本行，串行化同一版本的并发操作。"""
        return await self.session.get(
            DocumentVersion,
            version_id,
            populate_existing=True,
            with_for_update=for_update,
        )

    async def get_by_sha256(
        self, document_id: UUID, sha256: str
    ) -> DocumentVersion | None:
        return await self.session.scalar(
            select(DocumentVersion).where(
                DocumentVersion.document_id == document_id,
                DocumentVersion.sha256 == sha256,
            )
        )

    async def get_published(self, document_id: UUID) -> DocumentVersion | None:
        return await self.session.scalar(
            select(DocumentVersion).where(
                DocumentVersion.document_id == document_id,
                DocumentVersion.status == "published",
            )
        )

    async def list_for_document(self, document_id: UUID) -> list[DocumentVersion]:
        """按创建时间从新到旧，时间相同时按 ID 稳定排序。"""
        versions = await self.session.scalars(
            select(DocumentVersion)
            .where(DocumentVersion.document_id == document_id)
            .order_by(DocumentVersion.created_at.desc(), DocumentVersion.id)
        )
        return list(versions)


def chunk_row(
    chunk: Chunk, version: DocumentVersion, *, external_id: str | None = None
) -> dict[str, Any]:
    """把解析结果中的切片转换为 document_chunks 的一行。"""
    return {
        "version_id": version.id,
        "document_id": version.document_id,
        "external_id": external_id,
        "ordinal": chunk.ordinal,
        "chapter": chunk.chapter,
        "section": chunk.section,
        "article": chunk.article,
        "heading": chunk.heading,
        "part": chunk.part,
        "part_count": chunk.part_count,
        "content": chunk.content,
        "content_sha256": content_digest(chunk.content),
        "page_start": chunk.page_start,
        "page_end": chunk.page_end,
        "sources": list(chunk.sources),
    }


class DocumentChunkRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def replace_for_version(
        self,
        version: DocumentVersion,
        chunks: Sequence[Chunk],
        *,
        external_ids: Sequence[str] | None = None,
    ) -> int:
        """删除版本原有切片后整体写入，并同步 chunk_count。

        与版本状态更新放在同一事务中：重新解析失败时整体回滚，不会留下新旧混合的切片。
        """
        if external_ids is not None and len(external_ids) != len(chunks):
            raise ValueError("external_ids 与切片数量不一致")
        await self.session.execute(
            delete(DocumentChunk).where(DocumentChunk.version_id == version.id)
        )
        rows = [
            chunk_row(
                chunk,
                version,
                external_id=external_ids[index] if external_ids else None,
            )
            for index, chunk in enumerate(chunks)
        ]
        if rows:
            # 不取回自增主键，MySQL 可批量执行；逐个添加 ORM 对象会每行一次往返
            await self.session.execute(insert(DocumentChunk), rows)
        version.chunk_count = len(rows)
        await self.session.flush()
        return len(rows)

    async def list_for_version(
        self, version_id: UUID, *, offset: int, limit: int
    ) -> tuple[list[DocumentChunk], int]:
        total = await self.session.scalar(
            select(func.count())
            .select_from(DocumentChunk)
            .where(DocumentChunk.version_id == version_id)
        )
        chunks = await self.session.scalars(
            select(DocumentChunk)
            .where(DocumentChunk.version_id == version_id)
            .order_by(DocumentChunk.ordinal)
            .offset(offset)
            .limit(limit)
        )
        return list(chunks), int(total or 0)
