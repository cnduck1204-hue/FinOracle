"""版本发布与下架。只有已发布版本进入用户检索；检索按已发布版本 ID 过滤向量。

发布时在同一事务中把原发布版本归档，数据库唯一约束保证每个制度最多一个已发布版本。
归档版本的切片与原件保留，供历史答案回溯；它的向量在提交后清理，失败不影响检索结果。
"""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.db.base import utc_now
from app.models.user import User
from app.rag.indexing import VectorStore
from app.repositories.documents import DocumentRepository, DocumentVersionRepository
from app.repositories.import_jobs import ImportJobRepository
from app.schemas.ingestion import VersionResponse
from app.services.document_errors import version_state_conflict
from app.services.document_imports import delete_vectors_quietly, lock_idle_version
from app.services.document_library import version_response


async def publish_version(
    version_id: UUID, user: User, session: AsyncSession, store: VectorStore
) -> VersionResponse:
    """发布已向量化的版本，替换该制度当前的发布版本。"""
    async with session.begin():
        version = await lock_idle_version(session, version_id)
        if version.status != "ready":
            raise version_state_conflict("只能发布已完成向量化的版本")
        # 锁定制度，串行化同一制度的并发发布
        await DocumentRepository(session).get(version.document_id, for_update=True)
        versions = DocumentVersionRepository(session)
        current = await versions.get_published(version.document_id, for_update=True)
        replaced = current.id if current is not None else None
        if current is not None:
            current.status = "archived"
            # 先归档原版本，释放唯一约束占用的位置
            await session.flush()
        version.status = "published"
        version.published_at = utc_now()
        version.published_by = user.id
        await session.flush()
        latest = await ImportJobRepository(session).latest_for_version(version_id)
        response = version_response(version, latest)
    if replaced is not None:
        await run_in_threadpool(delete_vectors_quietly, store, replaced)
    return response


async def withdraw_version(
    version_id: UUID, session: AsyncSession, store: VectorStore
) -> VersionResponse:
    """下架已发布版本：不再进入检索。之后可批准重新向量化并再次发布。"""
    async with session.begin():
        version = await lock_idle_version(session, version_id)
        if version.status != "published":
            raise version_state_conflict("只能下架已发布的版本")
        version.status = "archived"
        await session.flush()
        latest = await ImportJobRepository(session).latest_for_version(version_id)
        response = version_response(version, latest)
    await run_in_threadpool(delete_vectors_quietly, store, version_id)
    return response
