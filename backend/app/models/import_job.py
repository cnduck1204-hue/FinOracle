"""导入任务：worker 认领并执行的解析、索引与清理任务，同时供前端展示处理进度。"""

from datetime import datetime
from typing import Literal, get_args
from uuid import UUID, uuid4

from sqlalchemy import Enum, ForeignKey, Index, Integer, String, Uuid
from sqlalchemy.dialects.mysql import DATETIME
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, utc_now

JobKind = Literal["parse", "index", "cleanup"]
JobStatus = Literal["queued", "running", "succeeded", "failed", "cancelled"]
# 仍在排队或执行中的任务；同一版本此时不再接受新任务
ACTIVE_JOB_STATUSES: tuple[JobStatus, ...] = ("queued", "running")

JOB_KINDS: tuple[JobKind, ...] = get_args(JobKind)
JOB_STATUSES: tuple[JobStatus, ...] = get_args(JobStatus)
DEFAULT_MAX_ATTEMPTS = 3


class ImportJob(Base):
    __tablename__ = "import_jobs"

    __table_args__ = (
        # worker 按状态与可执行时间认领任务
        Index("ix_import_jobs_status_run_after", "status", "run_after"),
        Index("ix_import_jobs_version_id", "version_id"),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid4
    )
    version_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "document_versions.id",
            name="fk_import_jobs_version_id",
            ondelete="CASCADE",
        ),
    )
    kind: Mapped[str] = mapped_column(
        Enum(*JOB_KINDS, name="import_job_kind", validate_strings=True)
    )
    status: Mapped[str] = mapped_column(
        Enum(*JOB_STATUSES, name="import_job_status", validate_strings=True),
        default="queued",
        server_default="queued",
    )
    # downloading / converting / parsing / ocr / chunking / saving / embedding / indexing
    stage: Mapped[str | None] = mapped_column(String(32))
    progress_current: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0"
    )
    progress_total: Mapped[int | None] = mapped_column(Integer)
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    max_attempts: Mapped[int] = mapped_column(
        Integer,
        default=DEFAULT_MAX_ATTEMPTS,
        server_default=str(DEFAULT_MAX_ATTEMPTS),
    )
    # 最早可执行时间；失败退避后推迟
    run_after: Mapped[datetime] = mapped_column(DATETIME(fsp=6), default=utc_now)
    # 执行中的 worker 与租约到期时间；租约过期的任务可被其他 worker 重新认领
    locked_by: Mapped[str | None] = mapped_column(String(64))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6))
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(String(1000))

    # 发布后自动创建的清理任务等系统操作没有操作人
    created_by: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", name="fk_import_jobs_created_by")
    )
    created_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6), default=utc_now)
    started_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6))
    finished_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6))
    updated_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6), default=utc_now, onupdate=utc_now
    )
