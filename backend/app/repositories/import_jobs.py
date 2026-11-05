"""导入任务的数据库访问：登记、认领、续租与归属校验。"""

from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import ColumnElement, and_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from app.models.import_job import ACTIVE_JOB_STATUSES, ImportJob


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
