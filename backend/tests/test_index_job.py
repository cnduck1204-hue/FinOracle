from sqlalchemy import select

from app.core.config import ParsingSettings
from app.models import DocumentVersion, ImportJob
from app.rag.indexing import VectorRecord
from app.rag.indexing.errors import embedding_unavailable
from app.worker.index import IndexHandler
from app.worker.parse import ParseHandler
from app.worker.runtime import Worker
from tests.parsing_samples import make_pdf, text_image
from tests.worker_support import (
    FakeEmbedding,
    MemoryVectorStore,
    load,
    run_with_database,
    seed,
)

REGULATION = (
    "第一章 总则\n\n第一条 为了规范经营，制定本办法。\n\n"
    "第二条 本办法适用于金融机构。\n\n第三条 本办法自公布之日起施行。\n"
).encode()
SETTINGS = ParsingSettings(_env_file=None)


def make_worker(env, *, auto_approve=True, embedding=None, vectors=None):
    env.embedding = embedding or FakeEmbedding()
    env.vectors = vectors or MemoryVectorStore()
    return Worker(
        env.sessions,
        env.storage,
        [
            ParseHandler(SETTINGS, auto_approve=auto_approve),
            IndexHandler(env.embedding, env.vectors),
        ],
        worker_id="worker-1",
        heartbeat_seconds=0.05,
        clock=env.clock,
    )


async def drain(worker) -> None:
    while await worker.run_once():
        pass


async def jobs_of(env, version_id):
    async with env.sessions() as session:
        rows = await session.scalars(
            select(ImportJob)
            .where(ImportJob.version_id == version_id)
            .order_by(ImportJob.created_at)
        )
        return rows.all()


def test_parsed_version_is_indexed_automatically(tmp_path):
    async def scenario(env):
        ids = await seed(env, content=REGULATION)
        worker = make_worker(env)
        # 旧的向量应在写入前清除
        env.vectors.upsert(
            [VectorRecord(999, ids.document_id, ids.version_id, "旧", [0.0])]
        )
        await drain(worker)

        version = await load(env, DocumentVersion, ids.version_id)
        assert (version.status, version.index_model) == ("ready", "fake-embedding:4")
        assert version.indexed_at == env.clock()
        jobs = await jobs_of(env, ids.version_id)
        assert [(job.kind, job.status) for job in jobs] == [
            ("parse", "succeeded"),
            ("index", "succeeded"),
        ]
        assert (jobs[1].progress_current, jobs[1].progress_total) == (3, 3)
        records = env.vectors.by_version(ids.version_id)
        assert len(records) == 3 and 999 not in env.vectors.records
        assert all(r.document_id == ids.document_id for r in records)
        assert records[0].text.startswith("金融机构管理办法 第一章 总则 第")

    run_with_database(tmp_path, scenario)


def test_auto_approval_can_be_turned_off(tmp_path):
    async def scenario(env):
        ids = await seed(env, content=REGULATION)
        await drain(make_worker(env, auto_approve=False))
        assert (await load(env, DocumentVersion, ids.version_id)).status == "parsed"
        assert [job.kind for job in await jobs_of(env, ids.version_id)] == ["parse"]

    run_with_database(tmp_path, scenario)


def test_versions_with_pending_ocr_wait_for_manual_approval(tmp_path):
    async def scenario(env):
        pdf = make_pdf(
            tmp_path / "scan.pdf",
            [["第一条 文字页内容完整。"], [("image", text_image(), 450)]],
        )
        ids = await seed(env, file_name="扫描.pdf", content=pdf.read_bytes())
        await drain(make_worker(env))
        version = await load(env, DocumentVersion, ids.version_id)
        assert (version.status, version.parse_status) == ("parsed", "needs_ocr")
        assert [job.kind for job in await jobs_of(env, ids.version_id)] == ["parse"]

    run_with_database(tmp_path, scenario)


def test_embedding_outage_is_retried_without_failing_the_version(tmp_path):
    async def scenario(env):
        ids = await seed(env, content=REGULATION)
        embedding = FakeEmbedding()
        embedding.fail_with = embedding_unavailable()
        worker = make_worker(env, embedding=embedding)
        await drain(worker)

        index_job = (await jobs_of(env, ids.version_id))[1]
        assert (index_job.status, index_job.error_code) == (
            "queued",
            "EMBEDDING_UNAVAILABLE",
        )
        assert (await load(env, DocumentVersion, ids.version_id)).status == "indexing"

        embedding.fail_with = None
        env.clock.advance(30)
        await drain(worker)
        assert (await load(env, DocumentVersion, ids.version_id)).status == "ready"

    run_with_database(tmp_path, scenario)


def test_vector_store_outage_fails_after_the_last_attempt(tmp_path):
    async def scenario(env):
        ids = await seed(env, content=REGULATION)
        vectors = MemoryVectorStore()
        vectors.unavailable = True
        worker = make_worker(env, vectors=vectors)
        for delay in (0, 30, 120):
            env.clock.advance(delay)
            await drain(worker)
        index_job = (await jobs_of(env, ids.version_id))[1]
        assert (index_job.status, index_job.attempts, index_job.error_code) == (
            "failed",
            3,
            "VECTOR_STORE_UNAVAILABLE",
        )
        assert (await load(env, DocumentVersion, ids.version_id)).status == "failed"

    run_with_database(tmp_path, scenario)


def test_index_job_requires_an_indexing_version_with_chunks(tmp_path):
    async def scenario(env):
        moved = await seed(
            env,
            version_status="parsed",
            job={"kind": "index", "run_after": env.clock()},
        )
        empty = await seed(
            env,
            version_status="indexing",
            job={"kind": "index", "run_after": env.clock()},
        )
        await drain(make_worker(env))

        moved_job = (await jobs_of(env, moved.version_id))[0]
        assert (moved_job.status, moved_job.error_code) == (
            "failed",
            "VERSION_STATE_CONFLICT",
        )
        # 版本已不在待索引状态时保持原状
        assert (await load(env, DocumentVersion, moved.version_id)).status == "parsed"
        empty_job = (await jobs_of(env, empty.version_id))[0]
        assert (empty_job.status, empty_job.error_code) == ("failed", "DOCUMENT_EMPTY")
        assert (await load(env, DocumentVersion, empty.version_id)).status == "failed"
        assert env.vectors.records == {}

    run_with_database(tmp_path, scenario)


def test_index_job_for_a_missing_version_stops_cleanly(tmp_path):
    async def scenario(env):
        ids = await seed(env, version_status="indexing", job={"kind": "index"})
        async with env.sessions() as session, session.begin():
            await session.delete(await session.get(DocumentVersion, ids.version_id))
        # 任务随版本级联删除，worker 没有可执行的任务
        assert await make_worker(env).run_once() is False
        assert await load(env, ImportJob, ids.job_id) is None

    run_with_database(tmp_path, scenario)
