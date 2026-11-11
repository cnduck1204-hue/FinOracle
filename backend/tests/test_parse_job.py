import json

import pytest
from sqlalchemy import select

from app.core.config import ParsingSettings
from app.models import DocumentChunk, DocumentVersion, ImportJob
from app.rag.parsing import PARSER_VERSION
from app.storage.keys import blocks_key, converted_pdf_key
from app.worker.parse import ParseHandler
from app.worker.runtime import ClaimedJob, JobContext, LeaseLost, Worker
from tests.parsing_samples import OLE2_HEADER, FakeConverter
from tests.worker_support import NO_OCR_OPTIONS, load, run_with_database, seed

REGULATION = (
    "第一章 总则\n\n"
    "第一条 为了规范金融机构经营行为，制定本办法。\n\n"
    "第二条 本办法适用于境内金融机构。\n"
).encode()
SETTINGS = ParsingSettings(_env_file=None)


def make_worker(env, handler=None):
    return Worker(
        env.sessions,
        env.storage,
        [handler or ParseHandler(SETTINGS)],
        worker_id="worker-1",
        heartbeat_seconds=0.05,
        clock=env.clock,
    )


async def stored_chunks(env, version_id):
    async with env.sessions() as session:
        rows = await session.scalars(
            select(DocumentChunk)
            .where(DocumentChunk.version_id == version_id)
            .order_by(DocumentChunk.ordinal)
        )
        return rows.all()


def test_parse_job_saves_chunks_blocks_and_marks_version_parsed(tmp_path):
    async def scenario(env):
        ids = await seed(env, content=REGULATION)
        assert await make_worker(env).run_once()

        version = await load(env, DocumentVersion, ids.version_id)
        assert (version.status, version.parse_status) == ("parsed", "completed")
        assert version.chunk_count == 2
        assert version.parser_version == PARSER_VERSION
        assert version.page_count is None and version.pdf_object_key is None
        assert version.parse_stats["chunker"] == "article"
        assert version.parse_stats["ocr_provider"] is None
        assert version.parse_stats["chunks"] == 2
        assert version.parse_warnings == []

        chunks = await stored_chunks(env, ids.version_id)
        assert [(c.article, c.chapter) for c in chunks] == [
            ("第一条", "第一章 总则"),
            ("第二条", "第一章 总则"),
        ]
        blocks = json.loads(
            env.storage.objects[blocks_key(ids.document_id, ids.version_id)]
        )
        assert [block["text"] for block in blocks][0] == "第一章 总则"
        assert (
            env.storage.content_types[blocks_key(ids.document_id, ids.version_id)]
            == "application/json"
        )

        job = await load(env, ImportJob, ids.job_id)
        assert (job.status, job.error_code) == ("succeeded", None)

    run_with_database(tmp_path, scenario)


def test_reparse_replaces_previous_chunks(tmp_path):
    async def scenario(env):
        ids = await seed(env, content=REGULATION)
        worker = make_worker(env)
        await worker.run_once()
        async with env.sessions() as session, session.begin():
            version = await session.get(DocumentVersion, ids.version_id)
            version.parse_options = NO_OCR_OPTIONS | {
                "chunker": "length",
                "max_chunk_chars": 4000,
                "chunk_overlap": 0,
            }
            session.add(
                ImportJob(
                    version_id=ids.version_id, kind="parse", run_after=env.clock()
                )
            )
        await worker.run_once()

        version = await load(env, DocumentVersion, ids.version_id)
        chunks = await stored_chunks(env, ids.version_id)
        assert version.parse_stats["chunker"] == "length"
        assert version.chunk_count == len(chunks) == 1
        assert "第二条" in chunks[0].content

    run_with_database(tmp_path, scenario)


def test_legacy_document_is_converted_and_the_pdf_is_kept(tmp_path):
    async def scenario(env):
        ids = await seed(env, file_name="旧版规定.doc", content=OLE2_HEADER + b"word")
        converter = FakeConverter(
            [
                ["第一章 总则", "第一条 为了规范经营，制定本办法。"],
                ["第二条 适用范围。"],
            ]
        )
        handler = ParseHandler(SETTINGS, converter_factory=lambda settings: converter)
        await make_worker(env, handler).run_once()

        pdf_key = converted_pdf_key(ids.document_id, ids.version_id)
        version = await load(env, DocumentVersion, ids.version_id)
        assert version.status == "parsed"
        assert version.pdf_object_key == pdf_key
        assert version.page_count == 2
        assert env.storage.objects[pdf_key].startswith(b"%PDF")
        assert env.storage.content_types[pdf_key] == "application/pdf"
        assert converter.calls[0].name == "original.doc"
        chunks = await stored_chunks(env, ids.version_id)
        assert [(c.article, c.page_start) for c in chunks] == [
            ("第一条", 1),
            ("第二条", 2),
        ]

    run_with_database(tmp_path, scenario)


async def run_failing(env, **seed_values):
    ids = await seed(env, **seed_values)
    await make_worker(env).run_once()
    job = await load(env, ImportJob, ids.job_id)
    version = await load(env, DocumentVersion, ids.version_id)
    return job, version


def test_document_without_text_fails_without_retry(tmp_path):
    async def scenario(env):
        job, version = await run_failing(env, content=b"   \n\n  \n")
        assert (job.status, job.attempts, job.error_code) == (
            "failed",
            1,
            "DOCUMENT_EMPTY",
        )
        assert version.status == "failed" and version.chunk_count == 0

    run_with_database(tmp_path, scenario)


def test_missing_original_fails_without_retry(tmp_path):
    async def scenario(env):
        job, version = await run_failing(env, content=None)
        assert (job.status, job.error_code) == ("failed", "DOCUMENT_FILE_MISSING")
        assert version.status == "failed"

    run_with_database(tmp_path, scenario)


def test_corrupted_document_fails_without_retry(tmp_path):
    async def scenario(env):
        job, version = await run_failing(
            env, file_name="损坏.pdf", content=b"not really a pdf"
        )
        assert (job.status, job.error_code) == ("failed", "DOCUMENT_INVALID")
        assert version.status == "failed"

    run_with_database(tmp_path, scenario)


def test_invalid_stored_options_fail_without_retry(tmp_path):
    async def scenario(env):
        job, version = await run_failing(
            env, content=REGULATION, parse_options={"ocr": "unknown"}
        )
        assert (job.status, job.error_code) == ("failed", "PARSE_OPTIONS_INVALID")
        assert version.status == "failed"

    run_with_database(tmp_path, scenario)


def test_storage_outage_is_retried_and_the_version_stays_in_progress(tmp_path):
    async def scenario(env):
        ids = await seed(env, content=REGULATION)
        env.storage.unavailable = True
        await make_worker(env).run_once()
        job = await load(env, ImportJob, ids.job_id)
        version = await load(env, DocumentVersion, ids.version_id)
        assert (job.status, job.error_code) == ("queued", "STORAGE_UNAVAILABLE")
        assert version.status == "parsing"

        env.storage.unavailable = False
        env.clock.advance(30)
        await make_worker(env).run_once()
        assert (await load(env, DocumentVersion, ids.version_id)).status == "parsed"
        assert (await load(env, ImportJob, ids.job_id)).attempts == 2

    run_with_database(tmp_path, scenario)


def test_parse_stops_when_the_version_was_deleted(tmp_path):
    async def scenario(env):
        ids = await seed(env, content=REGULATION)
        handler = ParseHandler(SETTINGS)
        claimed = ClaimedJob(ids.job_id, ids.version_id, "parse", 1, 3, None)
        async with env.sessions() as session, session.begin():
            job = await session.get(ImportJob, ids.job_id)
            job.status, job.locked_by = "running", "worker-1"
            # 删除版本时其任务随之级联删除
            await session.delete(await session.get(DocumentVersion, ids.version_id))

        with pytest.raises(LeaseLost):
            await handler.run(JobContext(claimed, make_worker(env, handler)))
        assert list(env.storage.objects) == [ids.key]
        assert await stored_chunks(env, ids.version_id) == []

    run_with_database(tmp_path, scenario)
