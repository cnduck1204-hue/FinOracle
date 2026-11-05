"""导入任务的数据库访问：登记、认领、续租与归属校验。"""

from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import ColumnElement, Row, and_, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from app.models.document import Document, DocumentVersion
from app.models.import_job import ACTIVE_JOB_STATUSES, ImportJob

# 任务连同所属制度 ID、文件名与制度标题，供列表与详情展示
_WITH_CONTEXT = (
    select(
        ImportJob,
        DocumentVersion.document_id,
        DocumentVersion.file_name,
        Document.title,
    )
    .join(DocumentVersion, DocumentVersion.id == ImportJob.version_id)
    .join(Document, Document.id == DocumentVersion.document_id)
)


class ImportJobRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(self, job: ImportJob) -> None:
        self.session.add(job)
        await self.session.flush()

    async def get(self, job_id: UUID, *, for_update: bool = False) -> ImportJob | None:
        return await self.session.get(
            ImportJob, job_id, populate_existing=True, with_for_update=for_update
        )

    async def get_active_for_version(self, version_id: UUID) -> ImportJob | None:
        """版本仍在排队或执行中的任务；有则拒绝重复提交。调用方应先锁定版本行。"""
        return await self.session.scalar(
            select(ImportJob)
            .where(
                ImportJob.version_id == version_id,
                ImportJob.status.in_(ACTIVE_JOB_STATUSES),
            )
            .order_by(ImportJob.created_at.desc(), ImportJob.id)
            .limit(1)
        )

    async def get_with_context(self, job_id: UUID) -> Row | None:
        """返回 (任务, 制度 ID, 文件名, 制度标题)。"""
        result = await self.session.execute(_WITH_CONTEXT.where(ImportJob.id == job_id))
        return result.first()

    async def latest_for_version(self, version_id: UUID) -> Row | None:
        """版本最近一次的任务，行内容同 get_with_context。"""
        result = await self.session.execute(
            _WITH_CONTEXT.where(ImportJob.version_id == version_id)
            .order_by(ImportJob.created_at.desc(), ImportJob.id)
            .limit(1)
        )
        return result.first()

    async def list_page(
        self,
        *,
        status: str | None,
        kind: str | None,
        document_id: UUID | None,
        version_id: UUID | None,
        offset: int,
        limit: int,
    ) -> tuple[list[Row], int]:
        """按创建时间从新到旧，行内容同 get_with_context。"""
        filters = []
        if status:
            filters.append(ImportJob.status == status)
        if kind:
            filters.append(ImportJob.kind == kind)
        if document_id:
            filters.append(DocumentVersion.document_id == document_id)
        if version_id:
            filters.append(ImportJob.version_id == version_id)
        total = await self.session.scalar(
            select(func.count())
            .select_from(ImportJob)
            .join(DocumentVersion, DocumentVersion.id == ImportJob.version_id)
            .where(*filters)
        )
        result = await self.session.execute(
            _WITH_CONTEXT.where(*filters)
            .order_by(ImportJob.created_at.desc(), ImportJob.id)
            .offset(offset)
            .limit(limit)
        )
        return list(result.all()), int(total or 0)

    async def claim_next(
        self, *, worker_id: str, now: datetime, lease_seconds: float
    ) -> ImportJob | None:
        """认领一个任务：先接手租约已过期（worker 退出）的执行中任务，再取已到执行时间的排队任务。

        每次认领计一次尝试，worker 崩溃同样消耗尝试次数。调用方应立即提交。
        """
        job = await self._lock_first(
            and_(ImportJob.status == "running", ImportJob.lease_expires_at < now),
            ImportJob.lease_expires_at,
        ) or await self._lock_first(
            and_(ImportJob.status == "queued", ImportJob.run_after <= now),
            ImportJob.run_after,
        )
        if job is None:
            return None
        job.status = "running"
        job.locked_by = worker_id
        job.lease_expires_at = now + timedelta(seconds=lease_seconds)
        job.attempts += 1
        job.started_at = now
        job.finished_at = None
        await self.session.flush()
        return job

    async def _lock_first(
        self, condition: ColumnElement[bool], order: InstrumentedAttribute
    ) -> ImportJob | None:
        """锁定第一个满足条件、且未被其他事务锁定的任务。

        条件与排序须能按 (status, 排序列) 索引顺序读取（二级索引末尾隐含主键）。否则
        MySQL 先排序，FOR UPDATE 会锁住扫描到的全部行，并发的 worker 用 SKIP LOCKED
        时全部跳过，取不到任何任务。
        """
        return await self.session.scalar(
            select(ImportJob)
            .where(condition)
            .order_by(order, ImportJob.id)
            .limit(1)
            .with_for_update(skip_locked=True)
            .execution_options(populate_existing=True)
        )

    async def renew_lease(
        self,
        job_id: UUID,
        *,
        worker_id: str,
        now: datetime,
        lease_seconds: float,
        stage: str | None,
        progress_current: int,
        progress_total: int | None,
    ) -> bool:
        """续租并写入进度；任务已不归该 worker 持有时返回 False。"""
        result = await self.session.execute(
            update(ImportJob)
            .where(
                ImportJob.id == job_id,
                ImportJob.status == "running",
                ImportJob.locked_by == worker_id,
            )
            .values(
                lease_expires_at=now + timedelta(seconds=lease_seconds),
                stage=stage,
                progress_current=progress_current,
                progress_total=progress_total,
                updated_at=now,
            )
            .execution_options(synchronize_session=False)
        )
        return result.rowcount == 1

    async def get_owned(self, job_id: UUID, worker_id: str) -> ImportJob | None:
        """锁定任务并确认仍由该 worker 执行；写入结果前调用，防止租约被接管后重复写入。"""
        job = await self.get(job_id, for_update=True)
        if job is None or job.status != "running" or job.locked_by != worker_id:
            return None
        return job
