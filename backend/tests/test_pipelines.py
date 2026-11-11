import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.dependencies import require_current_user
from app.api.v1.endpoints.search import get_pipelines
from app.core.config import ParsingSettings, VisualSettings
from app.core.exceptions import AppException
from app.main import app
from app.models import DocumentChunk, DocumentVersion, ImportJob, VersionIndex
from app.rag.indexing import SearchHit, collection_name
from app.rag.indexing.errors import embedding_unavailable
from app.rag.pipelines import (
    BLOCK_INGEST,
    STRUCTURED_PIPELINE,
    VISUAL_PIPELINE,
    IngestPipeline,
    Pipeline,
    PipelineRegistry,
    build_pipelines,
)
from app.rag.pipelines.pdf_blocks import parse_pdf_blocks
from app.rag.pipelines.visual import (
    PageHit,
    VisualEmbeddingProvider,
    VisualHybridRetriever,
)
from app.rag.retrieval import HybridRetriever, reciprocal_rank_fusion
from app.schemas.search import SearchRequest
from app.services import document_imports
from app.services import pipelines as pipeline_service
from app.services import search as search_service
from app.worker.pipeline import PipelineHandler
from app.worker.runtime import Worker
from tests.parsing_samples import make_pdf
from tests.test_ingestion_api import make_user
from tests.worker_support import (
    FakeEmbedding,
    MemoryVectorStore,
    load,
    run_with_database,
    seed,
)

SETTINGS = ParsingSettings(_env_file=None)
LONG_LINE = "金融机构应当建立健全内部控制制度并定期开展检查"


class FakeVisual:
    model_id = "fake-visual:2"
    batch_pages = 2
    render_dpi = 36
    configured = True

    def __init__(self):
        self.pages = 0
        self.fail_with = None

    async def encode_images(self, images):
        if self.fail_with is not None:
            raise self.fail_with
        self.pages += len(images)
        return [[[1.0, 0.0], [0.0, 1.0]] for _ in images]

    async def encode_query(self, query):
        return [[1.0, 0.0]]


class MemoryPageStore:
    def __init__(self, hits=()):
        self.records = {}
        self.hits = list(hits)

    def ensure_collection(self):
        pass

    def upsert(self, records):
        for record in records:
            self.records[(record.version_id, record.page)] = record

    def delete_by_version(self, version_id):
        self.records = {
            key: value for key, value in self.records.items() if key[0] != version_id
        }

    def search(self, vectors, *, version_ids, limit):
        allowed = set(version_ids)
        return [hit for hit in self.hits if hit.version_id in allowed][:limit]


def make_registry(*, visual=None, pages=None):
    fakes = SimpleNamespace(
        embedding=FakeEmbedding(max_batch_size=10),
        store=MemoryVectorStore(),
        block_store=MemoryVectorStore(),
        visual=visual or FakeVisual(),
        pages=pages or MemoryPageStore(),
    )
    fakes.registry = build_pipelines(
        parsing=SETTINGS,
        embedding=fakes.embedding,
        store=fakes.store,
        block_store=fakes.block_store,
        visual=fakes.visual,
        pages=fakes.pages,
    )
    return fakes


def sample_pdf(tmp_path):
    return make_pdf(
        tmp_path / "source.pdf",
        [
            [LONG_LINE, "第二行内容说明了报送要求的具体安排"],
            [("table", [["数据类别", "报送频率"], ["资产负债", "按月"]])],
        ],
    ).read_bytes()


async def seed_pipeline_job(env, content, **job):
    return await seed(
        env,
        file_name="办法.pdf",
        content=content,
        version_status="published",
        job={"kind": "pipeline", "ingest": BLOCK_INGEST, **job},
    )


def make_worker(env, fakes):
    return Worker(
        env.sessions,
        env.storage,
        [PipelineHandler(fakes.registry)],
        worker_id="worker-1",
        heartbeat_seconds=0.05,
        clock=env.clock,
    )


async def drain(worker):
    while await worker.run_once():
        pass


async def chunks_of(env, version_id, ingest):
    async with env.sessions() as session:
        rows = await session.scalars(
            select(DocumentChunk)
            .where(
                DocumentChunk.version_id == version_id,
                DocumentChunk.ingest == ingest,
            )
            .order_by(DocumentChunk.ordinal)
        )
        return rows.all()


def test_block_parser_keeps_page_and_position(tmp_path):
    path = tmp_path / "doc.pdf"
    path.write_bytes(sample_pdf(tmp_path))
    chunks, pages = parse_pdf_blocks(path)
    assert pages == 2
    text = next(chunk for chunk in chunks if chunk.modality == "text")
    assert text.page_start == text.page_end == 1
    assert LONG_LINE in text.content
    x0, y0, x1, y1 = text.bbox
    assert 0 <= x0 < x1 and 0 <= y0 < y1
    table = next(chunk for chunk in chunks if chunk.modality == "table")
    assert table.page_start == 2
    assert "资产负债 | 按月" in table.content
    assert [chunk.ordinal for chunk in chunks] == list(range(1, len(chunks) + 1))


def test_a_pipeline_cannot_combine_parts_with_missing_channels():
    fakes = make_registry()
    structured = fakes.registry.get(STRUCTURED_PIPELINE)
    visual_retrieval = fakes.registry.get(VISUAL_PIPELINE).retrieval
    with pytest.raises(ValueError, match="visual"):
        Pipeline(
            name="bad",
            label="",
            description="",
            ingest=structured.ingest,
            retrieval=visual_retrieval,
        )


def test_two_pipelines_can_share_one_ingest():
    fakes = make_registry()
    block = fakes.registry.get(VISUAL_PIPELINE)
    text_only = Pipeline(
        name="pdf-block-text",
        label="",
        description="",
        ingest=block.ingest,
        retrieval=HybridRetriever(fakes.embedding, fakes.block_store),
    )
    registry = PipelineRegistry([block, text_only], default=VISUAL_PIPELINE)
    assert registry.get("pdf-block-text").ingest is registry.get(VISUAL_PIPELINE).ingest
    assert isinstance(registry.ingest(BLOCK_INGEST), IngestPipeline)


def test_registry_rejects_unknown_names():
    registry = make_registry().registry
    assert registry.get(None).name == STRUCTURED_PIPELINE
    with pytest.raises(AppException) as error:
        registry.get("nope")
    assert error.value.code == "PIPELINE_NOT_FOUND"


def test_ingests_use_separate_collections():
    assert collection_name("p", "m", 8) == "p_m_8"
    assert collection_name("p", "m", 8, namespace="pdf-block") == "p_m_8_pdf_block"


def test_pipeline_job_builds_its_own_chunks_and_indexes(tmp_path):
    content = sample_pdf(tmp_path)

    async def scenario(env):
        fakes = make_registry()
        ids = await seed_pipeline_job(env, content)
        await drain(make_worker(env, fakes))

        job = await load(env, ImportJob, ids.job_id)
        assert (job.status, job.error_code) == ("succeeded", None)
        index = await load(env, VersionIndex, (ids.version_id, BLOCK_INGEST))
        assert index.status == "ready" and index.page_count == 2
        assert index.index_model == "fake-embedding:4+fake-visual:2"
        version = await load(env, DocumentVersion, ids.version_id)
        # 版本生命周期和默认方案都不受影响
        assert (version.status, version.chunk_count) == ("published", 0)
        assert await chunks_of(env, ids.version_id, "structured") == []

        rows = await chunks_of(env, ids.version_id, BLOCK_INGEST)
        assert index.unit_count == len(rows) > 0
        assert rows[0].bbox is not None and rows[0].modality in ("text", "table")
        assert fakes.store.records == {}
        records = fakes.block_store.by_version(ids.version_id)
        assert len(records) == len(rows)
        assert all(record.text.startswith("金融机构管理办法") for record in records)
        assert sorted(page for _, page in fakes.pages.records) == [1, 2]
        assert fakes.visual.pages == 2

    run_with_database(tmp_path, scenario)


def test_rebuilding_replaces_previous_chunks_and_vectors(tmp_path):
    content = sample_pdf(tmp_path)

    async def scenario(env):
        fakes = make_registry()
        ids = await seed_pipeline_job(env, content)
        worker = make_worker(env, fakes)
        await drain(worker)
        first = len(await chunks_of(env, ids.version_id, BLOCK_INGEST))
        async with env.sessions() as session, session.begin():
            session.add(
                ImportJob(
                    version_id=ids.version_id,
                    kind="pipeline",
                    ingest=BLOCK_INGEST,
                    run_after=env.clock(),
                )
            )
        await drain(worker)
        rows = await chunks_of(env, ids.version_id, BLOCK_INGEST)
        assert len(rows) == first
        assert {r.chunk_id for r in fakes.block_store.by_version(ids.version_id)} == {
            row.id for row in rows
        }

    run_with_database(tmp_path, scenario)


def test_failed_pipeline_job_leaves_the_version_untouched(tmp_path):
    content = sample_pdf(tmp_path)

    async def scenario(env):
        fakes = make_registry()
        fakes.embedding.fail_with = embedding_unavailable()
        ids = await seed_pipeline_job(env, content, max_attempts=1)
        await drain(make_worker(env, fakes))

        job = await load(env, ImportJob, ids.job_id)
        assert (job.status, job.error_code) == ("failed", "EMBEDDING_UNAVAILABLE")
        index = await load(env, VersionIndex, (ids.version_id, BLOCK_INGEST))
        assert index.status == "failed"
        version = await load(env, DocumentVersion, ids.version_id)
        assert version.status == "published"

    run_with_database(tmp_path, scenario)


def test_non_pdf_sources_are_rejected_when_building(tmp_path):
    async def scenario(env):
        fakes = make_registry()
        ids = await seed(env, version_status="ready", job={"status": "succeeded"})
        user = await add_user(env)
        async with env.sessions() as session:
            with pytest.raises(AppException) as error:
                await pipeline_service.build_index(
                    ids.version_id, VISUAL_PIPELINE, user, session, fakes.registry
                )
        assert error.value.code == "PIPELINE_SOURCE_UNSUPPORTED"
        async with env.sessions() as session:
            states = await pipeline_service.list_for_version(
                ids.version_id, session, fakes.registry
            )
        assert {item.pipeline.name: item.supported for item in states} == {
            STRUCTURED_PIPELINE: True,
            VISUAL_PIPELINE: False,
        }

    run_with_database(tmp_path, scenario)


async def add_user(env):
    async with env.sessions() as session, session.begin():
        user = make_user("admin")
        session.add(user)
        await session.flush()
        return SimpleNamespace(id=user.id)


def test_build_index_validates_state_and_queues_one_job(tmp_path):
    content = sample_pdf(tmp_path)

    async def scenario(env):
        fakes = make_registry()
        user = await add_user(env)
        registry = fakes.registry

        async def build(version_id, name=VISUAL_PIPELINE):
            async with env.sessions() as session:
                return await pipeline_service.build_index(
                    version_id, name, user, session, registry
                )

        draft = await seed(
            env,
            file_name="办法.pdf",
            content=content,
            version_status="parsed",
            job={"status": "succeeded"},
        )
        with pytest.raises(AppException) as error:
            await build(draft.version_id)
        assert error.value.code == "VERSION_STATE_CONFLICT"

        ids = await seed(
            env,
            file_name="办法.pdf",
            content=content,
            version_status="published",
            job={"status": "succeeded"},
        )
        with pytest.raises(AppException) as error:
            await build(ids.version_id, STRUCTURED_PIPELINE)
        assert error.value.code == "VERSION_STATE_CONFLICT"

        job = await build(ids.version_id)
        assert (job.kind, job.ingest, job.status) == (
            "pipeline",
            BLOCK_INGEST,
            "queued",
        )
        with pytest.raises(AppException) as error:
            await build(ids.version_id)
        assert error.value.code == "IMPORT_IN_PROGRESS"

        async with env.sessions() as session:
            states = await pipeline_service.list_for_version(
                ids.version_id, session, registry
            )
        assert {item.pipeline.name: item.status for item in states} == {
            STRUCTURED_PIPELINE: "not_built",
            VISUAL_PIPELINE: "building",
        }

    run_with_database(tmp_path, scenario)


def test_failed_pipeline_job_can_be_retried(tmp_path):
    content = sample_pdf(tmp_path)

    async def scenario(env):
        fakes = make_registry()
        fakes.embedding.fail_with = embedding_unavailable()
        ids = await seed_pipeline_job(env, content, max_attempts=1)
        worker = make_worker(env, fakes)
        await drain(worker)
        fakes.embedding.fail_with = None

        async with env.sessions() as session:
            job = await document_imports.retry_job(ids.job_id, session)
        assert job.status == "queued"
        # 重试按真实时间排队，测试时钟停在过去
        async with env.sessions() as session, session.begin():
            record = await session.get(ImportJob, ids.job_id)
            record.run_after = env.clock()
        index = await load(env, VersionIndex, (ids.version_id, BLOCK_INGEST))
        version = await load(env, DocumentVersion, ids.version_id)
        assert (index.status, version.status) == ("building", "published")
        await drain(worker)
        index = await load(env, VersionIndex, (ids.version_id, BLOCK_INGEST))
        assert index.status == "ready"

    run_with_database(tmp_path, scenario)


def test_deleting_a_version_cleans_every_pipeline_index(tmp_path):
    content = sample_pdf(tmp_path)

    async def scenario(env):
        fakes = make_registry()
        ids = await seed_pipeline_job(env, content)
        await drain(make_worker(env, fakes))
        async with env.sessions() as session, session.begin():
            version = await session.get(DocumentVersion, ids.version_id)
            version.status = "ready"
        async with env.sessions() as session:
            await document_imports.delete_version(
                ids.version_id, session, env.storage, fakes.store, fakes.registry
            )
        assert fakes.block_store.by_version(ids.version_id) == []
        assert fakes.pages.records == {}
        assert await chunks_of(env, ids.version_id, BLOCK_INGEST) == []

    run_with_database(tmp_path, scenario)


def test_fusion_combines_three_channels():
    fused = reciprocal_rank_fusion(
        [SearchHit(1, 0.9), SearchHit(2, 0.8)],
        [SearchHit(2, 3.0)],
        visual=[SearchHit(2, 11.0), SearchHit(3, 9.0)],
        limit=10,
        k=60,
    )
    assert [item.chunk_id for item in fused] == [2, 1, 3]
    assert fused[0].visual_rank == 1 and fused[0].visual_score == 11.0
    assert fused[0].score == pytest.approx(1 / 62 + 1 / 61 + 1 / 61)


def test_visual_hits_expand_to_the_units_on_each_page():
    version = uuid4()
    pages = MemoryPageStore([PageHit(version, 2, 9.0), PageHit(version, 1, 5.0)])
    store = MemoryVectorStore()
    retriever = VisualHybridRetriever(
        FakeEmbedding(max_batch_size=10), store, FakeVisual(), pages
    )
    seen = []

    async def page_units(wanted):
        seen.extend(wanted)
        return [(10, version, 1), (21, version, 2), (20, version, 2)]

    results = asyncio.run(
        retriever.retrieve(
            "问题", version_ids=[version], limit=5, page_units=page_units
        )
    )
    assert seen == [(version, 2), (version, 1)]
    assert [item.chunk_id for item in results] == [20, 21, 10]
    assert [item.visual_score for item in results] == [9.0, 9.0, 5.0]


def test_visual_retrieval_needs_published_versions():
    retriever = VisualHybridRetriever(
        FakeEmbedding(), MemoryVectorStore(), FakeVisual(), MemoryPageStore()
    )
    assert asyncio.run(retriever.retrieve("问题", version_ids=[], limit=5)) == []


def test_search_only_covers_versions_indexed_by_the_chosen_pipeline(tmp_path):
    content = sample_pdf(tmp_path)

    async def scenario(env):
        fakes = make_registry()
        ids = await seed_pipeline_job(env, content)
        await drain(make_worker(env, fakes))
        rows = await chunks_of(env, ids.version_id, BLOCK_INGEST)
        fakes.pages.hits = [PageHit(ids.version_id, 2, 7.0)]
        other = await seed(
            env,
            file_name="其他.pdf",
            content=content,
            version_status="published",
            job={"status": "succeeded"},
        )

        async with env.sessions() as session:
            result = await search_service.search(
                SearchRequest(query="金融机构", pipeline=VISUAL_PIPELINE, top_k=20),
                session,
                fakes.registry,
            )
        assert result.pipeline == VISUAL_PIPELINE
        assert {item.chunk_id for item in result.results} == {row.id for row in rows}
        assert other.version_id not in {item.version.id for item in result.results}
        table = next(item for item in result.results if item.modality == "table")
        assert table.visual_rank is not None and table.bbox is not None

        async with env.sessions() as session:
            result = await search_service.search(
                SearchRequest(query="金融机构"), session, fakes.registry
            )
        assert (result.pipeline, result.results) == (STRUCTURED_PIPELINE, [])

    run_with_database(tmp_path, scenario)


def test_search_rejects_a_pipeline_that_is_not_configured(tmp_path):
    visual = VisualEmbeddingProvider(VisualSettings(_env_file=None))
    assert not visual.configured
    fakes = make_registry(visual=visual)

    async def scenario(env):
        async with env.sessions() as session:
            with pytest.raises(AppException) as error:
                await search_service.search(
                    SearchRequest(query="问题", pipeline=VISUAL_PIPELINE),
                    session,
                    fakes.registry,
                )
        assert error.value.code == "PIPELINE_UNAVAILABLE"

    run_with_database(tmp_path, scenario)


def test_visual_outage_is_reported_as_search_unavailable(tmp_path):
    content = sample_pdf(tmp_path)

    class Down(FakeVisual):
        async def encode_query(self, query):
            raise AppException(
                code="VISUAL_UNAVAILABLE", message="down", status_code=503
            )

    async def scenario(env):
        fakes = make_registry(visual=Down())
        ids = await seed_pipeline_job(env, content)
        await drain(make_worker(env, fakes))
        assert ids.version_id
        async with env.sessions() as session:
            with pytest.raises(AppException) as error:
                await search_service.search(
                    SearchRequest(query="问题", pipeline=VISUAL_PIPELINE),
                    session,
                    fakes.registry,
                )
        assert error.value.code == "SEARCH_UNAVAILABLE"

    run_with_database(tmp_path, scenario)


def test_pipelines_endpoint_lists_the_choices():
    fakes = make_registry()
    app.dependency_overrides[get_pipelines] = lambda: fakes.registry
    app.dependency_overrides[require_current_user] = lambda: SimpleNamespace(id=uuid4())
    try:
        response = TestClient(app).get("/api/v1/search/pipelines")
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200
    body = {item["name"]: item for item in response.json()}
    assert body[STRUCTURED_PIPELINE]["default"] is True
    assert body[VISUAL_PIPELINE]["channels"] == ["dense", "keyword", "visual"]
    assert body[VISUAL_PIPELINE]["available"] is True
