import asyncio
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models import Document, DocumentChunk, DocumentVersion, ImportJob
from app.models.document import content_digest
from app.models.user import utc_now
from app.rag.parsing.models import Chunk
from app.repositories.documents import (
    DocumentChunkRepository,
    DocumentRepository,
    DocumentVersionRepository,
    chunk_row,
)
from app.repositories.import_jobs import ImportJobRepository


@pytest.fixture
def db():
    engine = create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    with Session(engine) as storage:
        session = Mock(spec=AsyncSession)
        for name in ("get", "scalar", "scalars", "execute", "flush"):
            getattr(session, name).side_effect = getattr(storage, name)
        session.add.side_effect = storage.add
        yield SimpleNamespace(session=session, storage=storage)
    engine.dispose()


def run(coroutine):
    return asyncio.run(coroutine)


def add_document(db, **values) -> Document:
    document = Document(title="证券公司风险处置条例", **values)
    run(DocumentRepository(db.session).add(document))
    return document


def add_version(db, document, **values) -> DocumentVersion:
    version = DocumentVersion(
        document_id=document.id,
        file_name="条例.pdf",
        file_type="pdf",
        size_bytes=1024,
        sha256=values.pop("sha256", uuid4().hex * 2),
        object_key=f"documents/{document.id}/{uuid4()}/original.pdf",
        parse_options={"ocr": "auto", "chunker": "auto"},
        **values,
    )
    run(DocumentVersionRepository(db.session).add(version))
    return version


def parsed(ordinal: int, content: str, **values) -> Chunk:
    return Chunk(
        ordinal=ordinal,
        content=content,
        page_start=1,
        page_end=1,
        sources=["text"],
        **values,
    )


def test_document_lookup_by_id_and_external_id(db):
    document = add_document(db, external_id="P020240802341036118987")
    repository = DocumentRepository(db.session)

    assert run(repository.get(document.id)) is document
    assert run(repository.get(document.id, for_update=True)) is document
    assert run(repository.get_by_external_id("P020240802341036118987")) is document
    assert run(repository.get_by_external_id("missing")) is None
    assert run(repository.get(uuid4())) is None


def test_new_version_defaults(db):
    version = add_version(db, add_document(db))

    assert version.status == "uploaded"
    assert version.validity_status == "unknown"
    assert version.chunk_count == 0
    assert version.parse_status is None
    assert version.created_at == version.updated_at


def test_versions_are_unique_per_document_file(db):
    first = add_document(db)
    other = add_document(db)
    add_version(db, first, sha256="a" * 64)
    # 同一文件可以属于不同制度
    add_version(db, other, sha256="a" * 64)
    repository = DocumentVersionRepository(db.session)
    assert run(repository.get_by_sha256(first.id, "a" * 64)).document_id == first.id
    assert run(repository.get_by_sha256(first.id, "b" * 64)) is None

    with pytest.raises(IntegrityError):
        add_version(db, first, sha256="a" * 64)


def test_only_one_published_version_per_document(db):
    document = add_document(db)
    current = add_version(db, document, status="published")
    draft = add_version(db, document, status="ready")
    repository = DocumentVersionRepository(db.session)
    assert run(repository.get(current.id, for_update=True)) is current
    assert run(repository.get_published(document.id)) is current

    draft.status = "published"
    with pytest.raises(IntegrityError):
        db.storage.flush()
    db.storage.rollback()


def test_publishing_replacement_after_archiving_current_version(db):
    document = add_document(db)
    current = add_version(db, document, status="published")
    draft = add_version(db, document, status="ready")

    current.status = "archived"
    db.storage.flush()
    draft.status = "published"
    db.storage.flush()

    repository = DocumentVersionRepository(db.session)
    assert run(repository.get_published(document.id)) is draft
    # 其他制度不受影响
    assert run(repository.get_published(add_document(db).id)) is None


def test_versions_list_newest_first_with_stable_ties(db):
    document = add_document(db)
    same_time = datetime(2026, 11, 5, 8, 0)
    older = add_version(db, document, created_at=datetime(2026, 11, 4))
    tied = sorted(
        (add_version(db, document, created_at=same_time) for _ in range(2)),
        key=lambda version: version.id,
    )
    add_version(db, add_document(db))

    versions = run(DocumentVersionRepository(db.session).list_for_document(document.id))
    assert versions == [*tied, older]


def test_chunk_row_maps_every_parsed_field(db):
    version = add_version(db, add_document(db))
    chunk = parsed(
        3,
        "第七条 内容",
        chapter="第二章",
        section="第一节",
        article="第七条",
        heading="总则 / 细则",
        part=2,
        part_count=3,
    )

    row = chunk_row(chunk, version, external_id="doc_007")

    assert row == {
        "version_id": version.id,
        "document_id": version.document_id,
        "external_id": "doc_007",
        "ordinal": 3,
        "chapter": "第二章",
        "section": "第一节",
        "article": "第七条",
        "heading": "总则 / 细则",
        "part": 2,
        "part_count": 3,
        "content": "第七条 内容",
        "content_sha256": content_digest("第七条 内容"),
        "page_start": 1,
        "page_end": 1,
        "sources": ["text"],
    }


def test_replacing_chunks_removes_previous_parse_and_updates_count(db):
    version = add_version(db, add_document(db))
    sibling = add_version(db, add_document(db))
    repository = DocumentChunkRepository(db.session)
    run(repository.replace_for_version(sibling, [parsed(1, "其他版本")]))
    run(
        repository.replace_for_version(
            version, [parsed(i, f"旧{i}") for i in (1, 2, 3)]
        )
    )

    count = run(
        repository.replace_for_version(
            version, [parsed(1, "新一"), parsed(2, "新二")], external_ids=["c1", "c2"]
        )
    )

    stored = db.storage.scalars(
        select(DocumentChunk)
        .where(DocumentChunk.version_id == version.id)
        .order_by(DocumentChunk.ordinal)
    ).all()
    assert count == 2
    assert version.chunk_count == 2
    assert [(c.ordinal, c.content, c.external_id) for c in stored] == [
        (1, "新一", "c1"),
        (2, "新二", "c2"),
    ]
    assert all(c.document_id == version.document_id for c in stored)
    assert run(repository.list_for_version(sibling.id, offset=0, limit=10))[1] == 1


def test_replacing_with_no_chunks_clears_the_version(db):
    version = add_version(db, add_document(db))
    repository = DocumentChunkRepository(db.session)
    run(repository.replace_for_version(version, [parsed(1, "内容")]))

    assert run(repository.replace_for_version(version, [])) == 0
    assert version.chunk_count == 0
    assert run(repository.list_for_version(version.id, offset=0, limit=10)) == ([], 0)


def test_external_ids_must_match_chunks(db):
    version = add_version(db, add_document(db))
    with pytest.raises(ValueError):
        run(
            DocumentChunkRepository(db.session).replace_for_version(
                version, [parsed(1, "内容")], external_ids=[]
            )
        )
    db.session.execute.assert_not_called()


def test_chunk_pages_are_ordered_and_count_all_rows(db):
    version = add_version(db, add_document(db))
    repository = DocumentChunkRepository(db.session)
    run(
        repository.replace_for_version(
            version, [parsed(i, str(i)) for i in range(5, 0, -1)]
        )
    )

    page, total = run(repository.list_for_version(version.id, offset=1, limit=2))

    assert total == 5
    assert [chunk.ordinal for chunk in page] == [2, 3]


def test_chunk_document_must_match_its_version(db):
    version = add_version(db, add_document(db))
    db.storage.add(
        DocumentChunk(
            version_id=version.id,
            document_id=add_document(db).id,
            ordinal=1,
            content="内容",
            content_sha256=content_digest("内容"),
            sources=["text"],
        )
    )
    with pytest.raises(IntegrityError):
        db.storage.flush()
    db.storage.rollback()


def test_deleting_a_version_removes_its_chunks_and_jobs(db):
    version = add_version(db, add_document(db))
    run(
        DocumentChunkRepository(db.session).replace_for_version(
            version, [parsed(1, "内容")]
        )
    )
    run(
        ImportJobRepository(db.session).add(
            ImportJob(version_id=version.id, kind="parse")
        )
    )
    db.storage.commit()

    db.storage.delete(version)
    db.storage.commit()
    db.storage.expunge_all()

    assert db.storage.scalars(select(DocumentChunk)).all() == []
    assert db.storage.scalars(select(ImportJob)).all() == []


def test_new_job_defaults_to_queued_and_runnable(db):
    version = add_version(db, add_document(db))
    before = utc_now()
    job = ImportJob(version_id=version.id, kind="parse")
    run(ImportJobRepository(db.session).add(job))

    assert (job.status, job.attempts, job.max_attempts) == ("queued", 0, 3)
    assert job.progress_current == 0
    assert before <= job.run_after <= utc_now()
    assert run(ImportJobRepository(db.session).get(job.id, for_update=True)) is job


def test_active_job_lookup_ignores_finished_jobs(db):
    version = add_version(db, add_document(db))
    other = add_version(db, add_document(db))
    repository = ImportJobRepository(db.session)
    for status in ("succeeded", "failed", "cancelled"):
        run(
            repository.add(
                ImportJob(version_id=version.id, kind="parse", status=status)
            )
        )
    run(repository.add(ImportJob(version_id=other.id, kind="parse")))
    assert run(repository.get_active_for_version(version.id)) is None

    running = ImportJob(version_id=version.id, kind="index", status="running")
    run(repository.add(running))
    assert run(repository.get_active_for_version(version.id)) is running
