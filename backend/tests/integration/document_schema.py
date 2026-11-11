"""Check ingestion tables, constraints and repositories against the real MySQL schema."""

import asyncio
import sys
from datetime import date
from uuid import uuid4

from sqlalchemy import delete, select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.db import session as database
from app.models import Document, DocumentChunk, DocumentVersion, ImportJob
from app.models.document import content_digest
from app.rag.parsing.models import Chunk
from app.repositories.documents import (
    DocumentChunkRepository,
    DocumentRepository,
    DocumentVersionRepository,
)
from app.repositories.import_jobs import ImportJobRepository

DUPLICATE_KEY = 1062
ROW_IS_REFERENCED = 1451
NO_REFERENCED_ROW = 1452


def new_version(document: Document, **values) -> DocumentVersion:
    return DocumentVersion(
        document_id=document.id,
        file_name="证券公司风险处置条例.pdf",
        file_type="pdf",
        size_bytes=156_281,
        sha256=values.pop("sha256", uuid4().hex * 2),
        object_key=f"documents/{document.id}/{uuid4()}/original.pdf",
        parse_options={"ocr": "auto", "chunker": "auto", "max_chunk_chars": 800},
        **values,
    )


def chunk(ordinal: int, content: str) -> Chunk:
    return Chunk(
        ordinal=ordinal,
        chapter="第一章 总 则",
        article=f"第{ordinal}条",
        content=content,
        page_start=1,
        page_end=2,
        sources=["text", "ocr"],
    )


async def expect_error(code: int, operation) -> None:
    async with database.SessionFactory() as session:
        try:
            await operation(session)
            await session.flush()
        except IntegrityError as error:
            assert error.orig.args[0] == code, error.orig.args[0]
            await session.rollback()
        else:
            raise AssertionError(f"Expected MySQL error {code}")


async def check_documents() -> None:
    token = uuid4().hex
    document_ids = []

    try:
        async with database.SessionFactory() as session:
            document = Document(
                external_id=f"it_{token}", title="证券公司风险处置条例", issuer="国务院"
            )
            await DocumentRepository(session).add(document)
            other = Document(title="非银行支付机构监督管理条例")
            await DocumentRepository(session).add(other)
            document_ids += [document.id, other.id]
            versions = DocumentVersionRepository(session)
            current = new_version(
                document,
                status="published",
                sha256="a" * 64,
                version_note="2023年第二次修订",
                published_date=date(2023, 7, 20),
                parse_warnings=["第 3 页待 OCR"],
            )
            await versions.add(current)
            draft = new_version(document, status="ready", sha256="b" * 64)
            await versions.add(draft)
            # 同一文件可以登记到不同制度
            await versions.add(new_version(other, sha256="a" * 64))
            await session.commit()

        async with database.SessionFactory() as session:
            stored = await DocumentVersionRepository(session).get(current.id)
            assert stored.parse_options["max_chunk_chars"] == 800
            assert stored.parse_warnings == ["第 3 页待 OCR"]
            assert stored.published_date == date(2023, 7, 20)
            assert stored.validity_status == "unknown"
            found = await DocumentRepository(session).get_by_external_id(f"it_{token}")
            assert found.id == document.id and found.title == "证券公司风险处置条例"
        print("PASS: Documents and versions round-trip JSON, dates and defaults")

        async def duplicate_file(session):
            session.add(new_version(document, sha256="a" * 64))

        await expect_error(DUPLICATE_KEY, duplicate_file)
        print("PASS: Database rejects the same file twice for one document")

        async def second_publication(session):
            version = await session.get(DocumentVersion, draft.id)
            version.status = "published"

        await expect_error(DUPLICATE_KEY, second_publication)
        async with database.SessionFactory() as session:
            versions = DocumentVersionRepository(session)
            (await versions.get(current.id, for_update=True)).status = "archived"
            (await versions.get(draft.id, for_update=True)).status = "published"
            await session.commit()
        async with database.SessionFactory() as session:
            published = await DocumentVersionRepository(session).get_published(
                document.id
            )
            assert published.id == draft.id
            generated = await session.scalar(
                text(
                    "SELECT COUNT(*) FROM document_versions "
                    "WHERE published_document_id = :document_id"
                ),
                {"document_id": document.id.hex},
            )
            assert generated == 1
        print("PASS: One published version per document; archiving frees the slot")

        long_content = "处置证券公司风险过程中应当保障证券经纪业务正常进行。" * 1200
        async with database.SessionFactory() as session:
            version = await DocumentVersionRepository(session).get(draft.id)
            chunks = DocumentChunkRepository(session)
            await chunks.replace_for_version(
                version, [chunk(i, f"旧条文{i}") for i in range(1, 4)]
            )
            parsed = [chunk(i, f"第{i}条正文") for i in range(1, 300)]
            parsed.append(chunk(300, long_content))
            assert await chunks.replace_for_version(version, parsed) == 300
            await session.commit()
        async with database.SessionFactory() as session:
            page, total = await DocumentChunkRepository(session).list_for_version(
                draft.id, offset=298, limit=10
            )
            assert total == 300
            assert [item.ordinal for item in page] == [299, 300]
            assert page[1].content == long_content
            assert page[1].content_sha256 == content_digest(long_content)
            assert page[1].sources == ["text", "ocr"]
            assert page[1].document_id == document.id
            version = await DocumentVersionRepository(session).get(draft.id)
            assert version.chunk_count == 300
        print("PASS: Chunk replacement is complete, ordered and stores long articles")

        async def mismatched_chunk(session):
            session.add(
                DocumentChunk(
                    version_id=draft.id,
                    document_id=other.id,
                    ordinal=999,
                    content="内容",
                    content_sha256=content_digest("内容"),
                    sources=["text"],
                )
            )

        await expect_error(NO_REFERENCED_ROW, mismatched_chunk)
        print("PASS: Chunk document must match its version")

        async with database.SessionFactory() as session:
            jobs = ImportJobRepository(session)
            await jobs.add(
                ImportJob(version_id=draft.id, kind="parse", status="failed")
            )
            assert await jobs.get_active_for_version(draft.id) is None
            queued = ImportJob(version_id=draft.id, kind="index")
            await jobs.add(queued)
            assert (await jobs.get_active_for_version(draft.id)).id == queued.id
            await session.commit()

        async def delete_document(session):
            await session.execute(delete(Document).where(Document.id == document.id))

        await expect_error(ROW_IS_REFERENCED, delete_document)
        async with database.SessionFactory() as session:
            await session.execute(
                delete(DocumentVersion).where(DocumentVersion.id == draft.id)
            )
            await session.commit()
        async with database.SessionFactory() as session:
            for model in (DocumentChunk, ImportJob):
                remaining = await session.scalars(
                    select(model.id).where(model.version_id == draft.id)
                )
                assert remaining.all() == []
        print("PASS: Documents with versions are protected; version deletion cascades")
    finally:
        async with database.SessionFactory() as session:
            await session.execute(
                delete(DocumentVersion).where(
                    DocumentVersion.document_id.in_(document_ids)
                )
            )
            await session.execute(delete(Document).where(Document.id.in_(document_ids)))
            await session.commit()
        async with database.SessionFactory() as session:
            for model, column in (
                (Document, Document.id),
                (DocumentVersion, DocumentVersion.document_id),
                (DocumentChunk, DocumentChunk.document_id),
            ):
                left = await session.scalars(
                    select(model).where(column.in_(document_ids))
                )
                assert left.all() == []
        print("PASS: Only this run's documents, versions, chunks and jobs were deleted")


async def main():
    try:
        await asyncio.wait_for(check_documents(), timeout=60)
    finally:
        await database.engine.dispose()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (AssertionError, SQLAlchemyError, TimeoutError) as error:
        original = getattr(error, "orig", error)
        args = getattr(original, "args", ())
        code = args[0] if args and isinstance(args[0], int) else "unavailable"
        print(f"FAIL: {type(error).__name__}; database error code={code}")
        sys.exit(1)
