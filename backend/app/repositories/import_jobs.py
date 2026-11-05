"""导入任务的数据库访问。认领、续租与重试在任务框架中实现。"""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

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
