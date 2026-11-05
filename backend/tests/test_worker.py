import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import update
from sqlalchemy.exc import OperationalError

from app.core.exceptions import AppException
from app.models import DocumentVersion, ImportJob
from app.repositories.import_jobs import ImportJobRepository
from app.storage.errors import storage_unavailable
from app.worker.runtime import LeaseLost, Worker, classify
from tests.worker_support import T0, load, run_with_database, seed


class FakeHandler:
    """记录调用的处理器；behavior 决定 run 的行为。"""

    def __init__(self, behavior=None, kind="parse"):
        self.kind = kind
        self.behavior = behavior
        self.runs = 0
        self.failures = 0

    async def run(self, context):
        self.runs += 1
        if self.behavior is not None:
            await self.behavior(context)
            return
        async with context.sessions() as session, session.begin():
            job = await context.lock_job(session)
            context.report("parsing", 2, 4)
            context.succeed(job)

    async def on_failure(self, session, context):
        self.failures += 1
        version = await session.get(DocumentVersion, context.job.version_id)
        version.status = "failed"


def make_worker(env, *handlers, worker_id="worker-1", **options):
    values = {"poll_seconds": 0.01, "lease_seconds": 60, "heartbeat_seconds": 0.05}
    return Worker(
        env.sessions,
        env.storage,
        handlers,
        worker_id=worker_id,
        clock=env.clock,
        **(values | options),
    )


def raising(error):
    async def behavior(context):
        raise error

    return behavior


def test_claim_takes_due_and_abandoned_jobs_in_order(tmp_path):
    async def scenario(env):
        due = await seed(env, job={"run_after": T0 - timedelta(seconds=10)})
        await seed(env, job={"run_after": T0 + timedelta(seconds=60)})
        await seed(
            env,
            job={
                "status": "running",
                "locked_by": "busy",
                "lease_expires_at": T0 + timedelta(seconds=30),
            },
        )
        abandoned = await seed(
            env,
            job={
                "status": "running",
                "locked_by": "crashed",
                "attempts": 1,
                "run_after": T0 - timedelta(seconds=20),
                "lease_expires_at": T0 - timedelta(seconds=1),
            },
        )
        claimed = []
        for _ in range(3):
            async with env.sessions() as session, session.begin():
                job = await ImportJobRepository(session).claim_next(
                    worker_id="worker-1", now=T0, lease_seconds=60
                )
                claimed.append(job and (job.id, job.attempts, job.locked_by))
                if job is not None:
                    assert job.lease_expires_at == T0 + timedelta(seconds=60)
                    assert job.started_at == T0 and job.status == "running"
        assert claimed == [
            (abandoned.job_id, 2, "worker-1"),
            (due.job_id, 1, "worker-1"),
            None,
        ]

    run_with_database(tmp_path, scenario)


def test_lease_renewal_and_result_writes_require_ownership(tmp_path):
    async def scenario(env):
        ids = await seed(env)
        async with env.sessions() as session, session.begin():
            await ImportJobRepository(session).claim_next(
                worker_id="worker-1", now=T0, lease_seconds=60
            )
        later = T0 + timedelta(seconds=20)
        async with env.sessions() as session, session.begin():
            jobs = ImportJobRepository(session)
            renew = {
                "now": later,
                "lease_seconds": 60,
                "stage": "ocr",
                "progress_current": 3,
                "progress_total": 9,
            }
            assert not await jobs.renew_lease(ids.job_id, worker_id="other", **renew)
            assert await jobs.renew_lease(ids.job_id, worker_id="worker-1", **renew)
            assert await jobs.get_owned(ids.job_id, "other") is None
            assert (await jobs.get_owned(ids.job_id, "worker-1")).id == ids.job_id
        job = await load(env, ImportJob, ids.job_id)
        assert job.lease_expires_at == later + timedelta(seconds=60)
        assert (job.stage, job.progress_current, job.progress_total) == ("ocr", 3, 9)

    run_with_database(tmp_path, scenario)


def test_idle_worker_reports_no_work(tmp_path):
    async def scenario(env):
        assert await make_worker(env, FakeHandler()).run_once() is False

    run_with_database(tmp_path, scenario)


def test_successful_job_is_completed_by_the_handler(tmp_path):
    async def scenario(env):
        ids = await seed(env)
        handler = FakeHandler()
        assert await make_worker(env, handler).run_once() is True
        job = await load(env, ImportJob, ids.job_id)
        assert handler.runs == 1 and handler.failures == 0
        assert (job.status, job.attempts, job.finished_at) == ("succeeded", 1, T0)
        assert (job.locked_by, job.lease_expires_at, job.stage) == (None, None, None)
        assert (job.progress_current, job.progress_total) == (4, 4)

    run_with_database(tmp_path, scenario)


def test_retryable_failures_back_off_then_fail(tmp_path):
    async def scenario(env):
        ids = await seed(env)
        handler = FakeHandler(raising(storage_unavailable()))
        worker = make_worker(env, handler)

        assert await worker.run_once()
        job = await load(env, ImportJob, ids.job_id)
        assert (job.status, job.attempts, job.error_code) == (
            "queued",
            1,
            "STORAGE_UNAVAILABLE",
        )
        assert job.run_after == T0 + timedelta(seconds=30)
        assert job.locked_by is None
        # 退避期间不会被认领
        assert await worker.run_once() is False

        env.clock.advance(30)
        assert await worker.run_once()
        job = await load(env, ImportJob, ids.job_id)
        assert (job.status, job.attempts) == ("queued", 2)
        assert job.run_after == env.clock() + timedelta(seconds=120)
        assert handler.failures == 0

        env.clock.advance(120)
        assert await worker.run_once()
        job = await load(env, ImportJob, ids.job_id)
        assert (job.status, job.attempts, job.finished_at) == ("failed", 3, env.clock())
        assert job.error_message == "文件存储暂时不可用，请稍后重试"
        assert handler.failures == 1
        assert (await load(env, DocumentVersion, ids.version_id)).status == "failed"

    run_with_database(tmp_path, scenario)


def test_business_errors_fail_without_retry(tmp_path):
    async def scenario(env):
        ids = await seed(env)
        error = AppException(
            code="DOCUMENT_INVALID", message="文件已损坏", status_code=422
        )
        handler = FakeHandler(raising(error))
        await make_worker(env, handler).run_once()
        job = await load(env, ImportJob, ids.job_id)
        assert (job.status, job.attempts, job.error_code, job.error_message) == (
            "failed",
            1,
            "DOCUMENT_INVALID",
            "文件已损坏",
        )
        assert handler.failures == 1

    run_with_database(tmp_path, scenario)


def test_unexpected_errors_are_retried_with_a_generic_code(tmp_path):
    async def scenario(env):
        ids = await seed(env)
        await make_worker(env, FakeHandler(raising(ValueError("bug")))).run_once()
        job = await load(env, ImportJob, ids.job_id)
        assert (job.status, job.error_code, job.error_message) == (
            "queued",
            "JOB_INTERNAL_ERROR",
            "任务执行出错",
        )

    run_with_database(tmp_path, scenario)


def test_unsupported_job_kind_fails(tmp_path):
    async def scenario(env):
        ids = await seed(env, job={"kind": "index"})
        handler = FakeHandler()
        await make_worker(env, handler).run_once()
        job = await load(env, ImportJob, ids.job_id)
        assert (job.status, job.error_code) == ("failed", "JOB_KIND_UNSUPPORTED")
        assert handler.runs == 0

    run_with_database(tmp_path, scenario)


def test_reclaimed_job_without_attempts_left_fails_without_running(tmp_path):
    async def scenario(env):
        ids = await seed(
            env,
            job={
                "status": "running",
                "locked_by": "crashed",
                "attempts": 3,
                "lease_expires_at": T0 - timedelta(seconds=1),
            },
        )
        handler = FakeHandler()
        await make_worker(env, handler).run_once()
        job = await load(env, ImportJob, ids.job_id)
        assert (job.status, job.attempts, job.error_code) == (
            "failed",
            4,
            "JOB_ATTEMPTS_EXHAUSTED",
        )
        assert handler.runs == 0 and handler.failures == 1

    run_with_database(tmp_path, scenario)


def test_result_is_discarded_after_another_worker_takes_over(tmp_path):
    async def scenario(env):
        ids = await seed(env)

        async def taken_over(context):
            async with env.sessions() as session, session.begin():
                await session.execute(
                    update(ImportJob)
                    .where(ImportJob.id == ids.job_id)
                    .values(locked_by="worker-2")
                )
            async with env.sessions() as session, session.begin():
                job = await context.lock_job(session)
                context.succeed(job)

        handler = FakeHandler(taken_over)
        await make_worker(env, handler).run_once()
        job = await load(env, ImportJob, ids.job_id)
        assert (job.status, job.locked_by) == ("running", "worker-2")
        assert handler.failures == 0

    run_with_database(tmp_path, scenario)


def test_lock_job_raises_lease_lost_inside_the_callers_transaction(tmp_path):
    async def scenario(env):
        ids = await seed(env)
        seen = []

        async def check(context):
            async with env.sessions() as session:
                await session.execute(
                    update(ImportJob)
                    .where(ImportJob.id == ids.job_id)
                    .values(status="cancelled")
                )
                with pytest.raises(LeaseLost):
                    await context.lock_job(session)
                await session.rollback()
            seen.append(True)
            async with env.sessions() as session, session.begin():
                context.succeed(await context.lock_job(session))

        await make_worker(env, FakeHandler(check)).run_once()
        assert seen == [True]
        assert (await load(env, ImportJob, ids.job_id)).status == "succeeded"

    run_with_database(tmp_path, scenario)


def test_heartbeat_renews_the_lease_and_publishes_progress(tmp_path):
    async def scenario(env):
        ids = await seed(env)
        observed = {}

        async def slow(context):
            context.report("ocr", 1, 3)
            env.clock.advance(10)
            await asyncio.sleep(0.25)
            observed["job"] = await load(env, ImportJob, ids.job_id)
            async with env.sessions() as session, session.begin():
                context.succeed(await context.lock_job(session))

        await make_worker(env, FakeHandler(slow)).run_once()
        running = observed["job"]
        assert (running.stage, running.progress_current, running.progress_total) == (
            "ocr",
            1,
            3,
        )
        assert running.lease_expires_at == T0 + timedelta(seconds=70)
        assert (await load(env, ImportJob, ids.job_id)).status == "succeeded"

    run_with_database(tmp_path, scenario)


def test_run_loop_survives_storage_outage_and_stops_on_request(tmp_path):
    async def scenario(env):
        ids = await seed(env)
        env.storage.unavailable = True
        stop = asyncio.Event()
        handler = FakeHandler()
        worker = make_worker(env, handler)
        task = asyncio.create_task(worker.run(stop))
        for _ in range(100):
            if handler.runs:
                break
            await asyncio.sleep(0.01)
        stop.set()
        await asyncio.wait_for(task, timeout=2)
        assert handler.runs == 1
        assert (await load(env, ImportJob, ids.job_id)).status == "succeeded"

    run_with_database(tmp_path, scenario)


def test_failures_classify_by_retry_value():
    unavailable = AppException(code="X", message="m", status_code=503)
    invalid = AppException(code="Y", message="n", status_code=422)
    assert classify(unavailable) == ("X", "m", True)
    assert classify(invalid) == ("Y", "n", False)
    assert classify(OperationalError("SELECT 1", {}, Exception()))[::2] == (
        "JOB_DEPENDENCY_ERROR",
        True,
    )
    assert classify(OSError("disk"))[2] is True
    assert classify(RuntimeError("bug"))[0] == "JOB_INTERNAL_ERROR"


def test_lease_must_cover_several_heartbeats(tmp_path):
    with pytest.raises(ValueError):
        Worker(None, None, [], lease_seconds=10, heartbeat_seconds=5)


def database_down(*args, **kwargs):
    raise OperationalError("SELECT 1", {}, Exception("connection lost"))


def test_run_loop_keeps_polling_after_database_errors(tmp_path, monkeypatch):
    async def scenario(env):
        worker = make_worker(env, FakeHandler())
        stop = asyncio.Event()
        calls = []

        async def flaky_run_once():
            calls.append(True)
            if len(calls) == 3:
                stop.set()
            raise OperationalError("SELECT 1", {}, Exception("connection lost"))

        monkeypatch.setattr(worker, "run_once", flaky_run_once)
        await asyncio.wait_for(worker.run(stop), timeout=2)
        assert len(calls) == 3

    run_with_database(tmp_path, scenario)


def test_heartbeat_errors_do_not_interrupt_the_job(tmp_path, monkeypatch):
    async def scenario(env):
        ids = await seed(env)
        monkeypatch.setattr(ImportJobRepository, "renew_lease", database_down)

        async def slow(context):
            await asyncio.sleep(0.2)
            async with env.sessions() as session, session.begin():
                context.succeed(await context.lock_job(session))

        await make_worker(env, FakeHandler(slow)).run_once()
        assert (await load(env, ImportJob, ids.job_id)).status == "succeeded"

    run_with_database(tmp_path, scenario)


def test_unrecorded_failure_keeps_the_lease_for_a_later_retry(tmp_path, monkeypatch):
    async def scenario(env):
        ids = await seed(env)
        handler = FakeHandler(raising(storage_unavailable()))
        worker = make_worker(env, handler)

        original = ImportJobRepository.get_owned
        monkeypatch.setattr(ImportJobRepository, "get_owned", database_down)
        await worker.run_once()
        monkeypatch.setattr(ImportJobRepository, "get_owned", original)

        job = await load(env, ImportJob, ids.job_id)
        assert (job.status, job.locked_by) == ("running", "worker-1")
        assert job.lease_expires_at == T0 + timedelta(seconds=60)
        # 租约到期后任务被重新认领
        env.clock.advance(61)
        env.storage.unavailable = False
        worker.handlers["parse"] = FakeHandler()
        assert await worker.run_once()
        job = await load(env, ImportJob, ids.job_id)
        assert (job.status, job.attempts) == ("succeeded", 2)

    run_with_database(tmp_path, scenario)
