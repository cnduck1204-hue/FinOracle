"""任务执行框架：认领任务、续租并上报进度、按错误类型重试或标记失败。

每个 worker 进程同时执行一个任务。处理器在线程中执行耗时的解析与 OCR，事件循环同时负责续租。
写入结果前处理器在同一事务中锁定任务并确认仍归本 worker 所有：租约过期被接管后，迟到的结果
不会覆盖新的执行。
"""

import asyncio
import logging
import os
import socket
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol
from uuid import UUID, uuid4

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.exceptions import AppException
from app.db.base import utc_now
from app.models.import_job import ImportJob
from app.repositories.import_jobs import ImportJobRepository
from app.storage import ObjectStorage

logger = logging.getLogger(__name__)

# 第 1、2 次失败后分别等待 30 秒、2 分钟再重试，更多次沿用最后一个间隔
RETRY_DELAYS_SECONDS = (30, 120)
HEARTBEAT_SECONDS = 5.0
_MESSAGE_LIMIT = 1000


class LeaseLost(Exception):
    """任务已不归本 worker 所有（租约过期后被其他 worker 接手），结果必须丢弃。"""


class _Progress:
    """处理器线程写入、心跳协程读取的最新进度。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._value: tuple[str | None, int, int | None] = (None, 0, None)

    def set(self, stage: str, current: int, total: int | None) -> None:
        with self._lock:
            self._value = (stage, current, total)

    def get(self) -> tuple[str | None, int, int | None]:
        with self._lock:
            return self._value


@dataclass(frozen=True, slots=True)
class ClaimedJob:
    id: UUID
    version_id: UUID
    kind: str
    attempt: int
    max_attempts: int
    created_by: UUID | None


class JobContext:
    """处理器可用的任务信息与操作。"""

    def __init__(self, job: ClaimedJob, worker: "Worker") -> None:
        self.job = job
        self.sessions = worker.sessions
        self.storage = worker.storage
        self.worker_id = worker.worker_id
        self._clock = worker.clock
        self._progress = _Progress()

    def report(self, stage: str, current: int = 0, total: int | None = None) -> None:
        """记录当前阶段与进度，由心跳定期写入数据库；可在任意线程调用。"""
        self._progress.set(stage, current, total)

    def progress(self) -> tuple[str | None, int, int | None]:
        return self._progress.get()

    def now(self) -> datetime:
        """worker 的时钟；处理器写入的任务与版本时间都用它。"""
        return self._clock()

    async def lock_job(self, session: AsyncSession) -> ImportJob:
        """在调用方事务中锁定任务；已不归本 worker 所有时抛出 LeaseLost，调用方事务随之回滚。"""
        job = await ImportJobRepository(session).get_owned(self.job.id, self.worker_id)
        if job is None:
            raise LeaseLost(str(self.job.id))
        return job

    def succeed(self, job: ImportJob) -> None:
        """与处理结果在同一事务中标记成功。"""
        now = self._clock()
        job.status = "succeeded"
        job.finished_at = now
        job.locked_by = None
        job.lease_expires_at = None
        job.stage = None
        job.error_code = None
        job.error_message = None
        stage, current, total = self.progress()
        if total is not None:
            job.progress_current = total
            job.progress_total = total


class Handler(Protocol):
    kind: str

    async def run(self, context: JobContext) -> None:
        """执行任务，并在保存结果的事务中调用 context.succeed。"""

    async def on_failure(self, session: AsyncSession, context: JobContext) -> None:
        """任务最终失败（不再重试）时在同一事务中更新相关记录，例如把版本置为 failed。"""


def classify(error: BaseException) -> tuple[str, str, bool]:
    """返回错误码、提示信息与是否可重试。4xx 业务错误重试也不会成功，其余错误按次数重试。"""
    if isinstance(error, AppException):
        return error.code, error.message, error.status_code >= 500
    if isinstance(error, SQLAlchemyError | OSError):
        return "JOB_DEPENDENCY_ERROR", "依赖服务暂时不可用", True
    return "JOB_INTERNAL_ERROR", "任务执行出错", True


def default_worker_id() -> str:
    return f"{socket.gethostname()[:40]}:{os.getpid()}:{uuid4().hex[:8]}"


class Worker:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        storage: ObjectStorage,
        handlers: Sequence[Handler],
        *,
        worker_id: str | None = None,
        poll_seconds: float = 2.0,
        lease_seconds: float = 60.0,
        heartbeat_seconds: float = HEARTBEAT_SECONDS,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        if heartbeat_seconds * 3 > lease_seconds:
            raise ValueError("租约至少应覆盖三次心跳")
        self.sessions = sessions
        self.storage = storage
        self.handlers = {handler.kind: handler for handler in handlers}
        self.worker_id = worker_id or default_worker_id()
        self.poll_seconds = poll_seconds
        self.lease_seconds = lease_seconds
        self.heartbeat_seconds = heartbeat_seconds
        self.clock = clock

    async def run(self, stop: asyncio.Event) -> None:
        """循环认领并执行任务，直到 stop 被设置；正在执行的任务会先完成。"""
        logger.info("worker_started: %s", self.worker_id)
        try:
            await asyncio.to_thread(self.storage.ensure_bucket)
        except AppException:
            # 存储暂不可用时任务会失败并按次数重试，不阻止 worker 启动
            logger.warning("worker_storage_unavailable_at_startup")
        while not stop.is_set():
            try:
                worked = await self.run_once()
            except (SQLAlchemyError, OSError):
                logger.warning("worker_claim_failed", exc_info=True)
                worked = False
            if not worked:
                try:
                    await asyncio.wait_for(stop.wait(), timeout=self.poll_seconds)
                except TimeoutError:
                    pass
        logger.info("worker_stopped: %s", self.worker_id)

    async def run_once(self) -> bool:
        """认领并执行一个任务；没有可执行的任务时返回 False。"""
        job = await self._claim()
        if job is None:
            return False
        await self._execute(job)
        return True

    async def _claim(self) -> ClaimedJob | None:
        async with self.sessions() as session, session.begin():
            job = await ImportJobRepository(session).claim_next(
                worker_id=self.worker_id,
                now=self.clock(),
                lease_seconds=self.lease_seconds,
            )
            if job is None:
                return None
            return ClaimedJob(
                id=job.id,
                version_id=job.version_id,
                kind=job.kind,
                attempt=job.attempts,
                max_attempts=job.max_attempts,
                created_by=job.created_by,
            )

    async def _execute(self, job: ClaimedJob) -> None:
        context = JobContext(job, self)
        handler = self.handlers.get(job.kind)
        logger.info("job_started: %s %s attempt %d", job.kind, job.id, job.attempt)
        if handler is None:
            await self._fail(
                context, None, "JOB_KIND_UNSUPPORTED", "不支持的任务类型", False
            )
            return
        if job.attempt > job.max_attempts:
            # 执行中的 worker 多次退出，租约到期后被重新认领，尝试次数已用尽
            await self._fail(
                context, handler, "JOB_ATTEMPTS_EXHAUSTED", "任务多次中断", False
            )
            return

        heartbeat = asyncio.create_task(self._heartbeat(context))
        try:
            await handler.run(context)
        except LeaseLost:
            logger.warning("job_lease_lost: %s", job.id)
            return
        except Exception as error:
            code, message, retryable = classify(error)
            # 业务错误只记录错误码；其他异常记录堆栈以便排查
            expected = isinstance(error, AppException)
            (logger.warning if expected else logger.error)(
                "job_failed: %s %s %s", job.kind, job.id, code, exc_info=not expected
            )
            await self._fail(context, handler, code, message, retryable)
            return
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
        logger.info("job_succeeded: %s %s", job.kind, job.id)

    async def _heartbeat(self, context: JobContext) -> None:
        while True:
            await asyncio.sleep(self.heartbeat_seconds)
            stage, current, total = context.progress()
            try:
                async with self.sessions() as session, session.begin():
                    owned = await ImportJobRepository(session).renew_lease(
                        context.job.id,
                        worker_id=self.worker_id,
                        now=self.clock(),
                        lease_seconds=self.lease_seconds,
                        stage=stage,
                        progress_current=current,
                        progress_total=total,
                    )
            except (SQLAlchemyError, OSError):
                # 数据库短暂不可用时继续执行；租约足够覆盖数次心跳失败
                logger.warning("job_heartbeat_failed: %s", context.job.id)
                continue
            if not owned:
                logger.warning("job_lease_lost: %s", context.job.id)
                return

    async def _fail(
        self,
        context: JobContext,
        handler: Handler | None,
        code: str,
        message: str,
        retryable: bool,
    ) -> None:
        try:
            async with self.sessions() as session, session.begin():
                job = await ImportJobRepository(session).get_owned(
                    context.job.id, self.worker_id
                )
                if job is None:
                    return
                now = self.clock()
                job.error_code = code
                job.error_message = message[:_MESSAGE_LIMIT]
                job.locked_by = None
                job.lease_expires_at = None
                if retryable and job.attempts < job.max_attempts:
                    delay = RETRY_DELAYS_SECONDS[
                        min(job.attempts, len(RETRY_DELAYS_SECONDS)) - 1
                    ]
                    job.status = "queued"
                    job.run_after = now + timedelta(seconds=delay)
                    return
                job.status = "failed"
                job.finished_at = now
                if handler is not None:
                    await handler.on_failure(session, context)
        except (SQLAlchemyError, OSError):
            # 无法记录结果时保留租约，到期后任务被重新认领
            logger.error("job_failure_not_recorded: %s", context.job.id, exc_info=True)
