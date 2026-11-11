"""Check job claiming on MySQL and run real parse jobs through the Compose worker."""

import asyncio
import sys
import tempfile
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

from sqlalchemy import delete, func, select
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import DatabaseSettings, StorageSettings
from app.db import session as database
from app.db.base import utc_now
from app.db.engine import (
    WORKER_ISOLATION_LEVEL,
    create_engine,
    create_session_factory,
)
from app.models import Document, DocumentChunk, DocumentVersion, ImportJob
from app.repositories.import_jobs import ImportJobRepository
from app.storage import S3ObjectStorage
from app.storage.keys import blocks_key, original_key, version_prefix
from tests.integration.containers import compose

BACKEND = Path(__file__).resolve().parents[2]
SAMPLE_DOC = BACKEND / "samples" / "parsing" / "04_旧版规定.doc"
OPTIONS = {
    "ocr": "none",
    "chunker": "auto",
    "max_chunk_chars": 800,
    "chunk_overlap": 100,
}


def host_storage() -> S3ObjectStorage:
    address = compose("port", "minio", "9000").splitlines()[0]
    settings = StorageSettings().model_copy(
        update={"s3_endpoint_url": f"http://{address}"}
    )
    return S3ObjectStorage(settings)


async def create_version(
    document_ids: list, file_name: str, *, run_after=None, jobs: int = 1
) -> tuple[DocumentVersion, list]:
    file_type = file_name.rsplit(".", 1)[-1]
    async with database.SessionFactory() as session, session.begin():
        document = Document(title=f"集成测试 {uuid4().hex[:8]}")
        session.add(document)
        await session.flush()
        document_ids.append(document.id)
        version = DocumentVersion(
            document_id=document.id,
            file_name=file_name,
            file_type=file_type,
            size_bytes=0,
            sha256=uuid4().hex * 2,
            object_key="pending",
            parse_options=OPTIONS,
        )
        session.add(version)
        await session.flush()
        version.object_key = original_key(document.id, version.id, file_type)
        created = [
            ImportJob(
                version_id=version.id, kind="parse", run_after=run_after or utc_now()
            )
            for _ in range(jobs)
        ]
        session.add_all(created)
        await session.flush()
        return version, [job.id for job in created]


async def check_claiming(document_ids: list) -> None:
    async with database.SessionFactory() as session:
        others = await session.scalar(
            select(func.count())
            .select_from(ImportJob)
            .where(ImportJob.status.in_(("queued", "running")))
        )
    if others:
        print(f"SKIP: {others} unfinished jobs exist; claim check would take them")
        return

    # 一天后才到期，容器中的 worker 不会认领这些任务
    future = utc_now() + timedelta(days=1)
    _, job_ids = await create_version(
        document_ids, "claim.txt", run_after=future, jobs=3
    )
    now = future + timedelta(seconds=1)

    # 与 worker 进程相同的隔离级别
    engine = create_engine(DatabaseSettings(), isolation_level=WORKER_ISOLATION_LEVEL)
    sessions = create_session_factory(engine)
    first = sessions()
    second = sessions()
    try:
        await first.begin()
        await second.begin()
        claimed_first = await ImportJobRepository(first).claim_next(
            worker_id="it-worker-1", now=now, lease_seconds=60
        )
        # 第一个事务尚未提交、仍锁着它认领的任务，第二个事务跳过该行
        claimed_second = await ImportJobRepository(second).claim_next(
            worker_id="it-worker-2", now=now, lease_seconds=60
        )
        assert claimed_first.id != claimed_second.id
        assert {claimed_first.id, claimed_second.id} <= set(job_ids)
        await first.commit()
        await second.commit()
    finally:
        await first.close()
        await second.close()
    print("PASS: Concurrent claims skip locked rows and take different jobs")

    try:
        await check_lease_takeover(sessions, job_ids, now)
    finally:
        await engine.dispose()


async def check_lease_takeover(sessions, job_ids: list, now) -> None:
    async with sessions() as session, session.begin():
        jobs = ImportJobRepository(session)
        third = await jobs.claim_next(
            worker_id="it-worker-3", now=now, lease_seconds=60
        )
        assert third.id in job_ids
        assert (
            await jobs.claim_next(worker_id="it-worker-3", now=now, lease_seconds=60)
            is None
        )
    async with sessions() as session, session.begin():
        reclaimed = await ImportJobRepository(session).claim_next(
            worker_id="it-worker-4", now=now + timedelta(seconds=61), lease_seconds=60
        )
        assert reclaimed.id in job_ids and reclaimed.attempts == 2
        reclaimed_id = reclaimed.id
    async with sessions() as session, session.begin():
        jobs = ImportJobRepository(session)
        assert await jobs.get_owned(reclaimed_id, "it-worker-1") is None
        assert (await jobs.get_owned(reclaimed_id, "it-worker-4")).id == reclaimed_id
        for job_id in job_ids:
            (await jobs.get(job_id)).status = "cancelled"
    print(
        "PASS: Running jobs stay locked until their lease expires, then are reclaimed"
    )


async def wait_for_job(job_id, timeout: float = 120) -> ImportJob:
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        async with database.SessionFactory() as session:
            job = await session.get(ImportJob, job_id)
        if job.status in ("succeeded", "failed"):
            return job
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError(f"Job still {job.status} at stage {job.stage}")
        await asyncio.sleep(1)


async def check_worker_container(document_ids: list, storage: S3ObjectStorage) -> None:
    check = (
        "import os; assert os.geteuid() != 0; "
        "assert 'JWT_SECRET_KEY' not in os.environ; "
        "assert 'ADMIN_PASSWORD' not in os.environ; print('ok')"
    )
    assert compose("exec", "-T", "worker", "python", "-c", check) == "ok"
    print("PASS: Worker runs as non-root without authentication secrets")

    version, (job_id,) = await create_version(document_ids, "旧版规定.doc")
    await asyncio.to_thread(storage.put_file, version.object_key, SAMPLE_DOC)
    job = await wait_for_job(job_id)
    assert (job.status, job.attempts, job.error_code) == ("succeeded", 1, None), (
        job.error_code
    )
    async with database.SessionFactory() as session:
        stored = await session.get(DocumentVersion, version.id)
        chunks = await session.scalar(
            select(func.count())
            .select_from(DocumentChunk)
            .where(DocumentChunk.version_id == version.id)
        )
        index_jobs = await session.scalar(
            select(func.count())
            .select_from(ImportJob)
            .where(ImportJob.version_id == version.id, ImportJob.kind == "index")
        )
    assert stored.parse_status == "completed"
    # 容器默认自动批准：解析成功时版本转入向量化并创建一个索引任务；向量化结果由 indexing 检查
    if stored.status == "parsed":
        assert index_jobs == 0
    else:
        assert stored.status in ("indexing", "ready", "failed") and index_jobs == 1
    assert chunks == stored.chunk_count > 0
    assert stored.page_count and stored.page_count >= 1
    assert stored.parse_stats["chunks"] == chunks
    pdf = await asyncio.to_thread(storage.stat, stored.pdf_object_key)
    assert pdf is not None and pdf.content_type == "application/pdf"
    blocks = await asyncio.to_thread(
        storage.stat, blocks_key(version.document_id, version.id)
    )
    assert blocks is not None and blocks.size > 0
    print(
        f"PASS: Worker converted the .doc through Gotenberg, saved the PDF and "
        f"{chunks} chunks, and handed the version to indexing"
    )

    broken, (broken_job_id,) = await create_version(document_ids, "损坏.pdf")
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "broken.pdf"
        path.write_bytes(b"not really a pdf")
        await asyncio.to_thread(storage.put_file, broken.object_key, path)
    job = await wait_for_job(broken_job_id)
    assert (job.status, job.attempts, job.error_code) == (
        "failed",
        1,
        "DOCUMENT_INVALID",
    )
    async with database.SessionFactory() as session:
        assert (await session.get(DocumentVersion, broken.id)).status == "failed"
    print("PASS: Invalid documents fail once without retries and mark the version")


async def cleanup(document_ids: list, storage: S3ObjectStorage) -> None:
    async with database.SessionFactory() as session, session.begin():
        versions = (
            await session.execute(
                select(DocumentVersion.document_id, DocumentVersion.id).where(
                    DocumentVersion.document_id.in_(document_ids)
                )
            )
        ).all()
        await session.execute(
            delete(DocumentVersion).where(DocumentVersion.document_id.in_(document_ids))
        )
        await session.execute(delete(Document).where(Document.id.in_(document_ids)))
    client = storage._client
    for document_id, version_id in versions:
        listing = await asyncio.to_thread(
            client.list_objects_v2,
            Bucket=storage.bucket,
            Prefix=version_prefix(document_id, version_id) + "/",
        )
        for item in listing.get("Contents", []):
            await asyncio.to_thread(storage.delete, item["Key"])
    print("PASS: Only this run's documents, jobs, chunks and objects were removed")


async def main() -> None:
    document_ids: list = []
    storage = host_storage()
    try:
        await check_claiming(document_ids)
        await check_worker_container(document_ids, storage)
    finally:
        try:
            await cleanup(document_ids, storage)
        finally:
            await database.engine.dispose()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (AssertionError, SQLAlchemyError, TimeoutError) as error:
        print(f"FAIL: {type(error).__name__}: {error}")
        sys.exit(1)
