"""制度文档管理：查询与修改制度、版本元数据，分页查看切片与任务，读取原件。"""

import mimetypes
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Literal
from urllib.parse import quote
from uuid import UUID

from sqlalchemy import Row
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.db.base import utc_now
from app.models.document import Document, DocumentVersion
from app.rag.parsing.errors import file_missing
from app.repositories.documents import (
    DocumentChunkRepository,
    DocumentRepository,
    DocumentVersionRepository,
)
from app.repositories.import_jobs import ImportJobRepository
from app.schemas.ingestion import (
    ChunkPage,
    ChunkResponse,
    DocumentPage,
    DocumentResponse,
    DocumentUpdate,
    JobPage,
    JobResponse,
    ValidityStatus,
    VersionBrief,
    VersionResponse,
    VersionUpdate,
)
from app.schemas.library import (
    FacetCount,
    LibraryDocumentPage,
    LibraryDocumentSummary,
    LibraryFacetsResponse,
    LibraryPublishedVersion,
)
from app.services.document_errors import (
    document_not_found,
    file_not_available,
    job_not_found,
    version_not_found,
)
from app.storage import ObjectStorage

# mimetypes 在部分系统上缺少的类型
_MEDIA_TYPES = {
    "md": "text/markdown",
    "markdown": "text/markdown",
    "doc": "application/msword",
    "odt": "application/vnd.oasis.opendocument.text",
}
# 浏览器可安全直接显示的类型；其余以附件下载，避免把上传内容当网页渲染
_INLINE_TYPES = frozenset({"application/pdf", "image/png", "image/jpeg"})


def media_type_for(file_type: str) -> str:
    return (
        _MEDIA_TYPES.get(file_type)
        or mimetypes.guess_type(f"file.{file_type}")[0]
        or "application/octet-stream"
    )


def content_disposition(file_name: str, media_type: str) -> str:
    """带 UTF-8 文件名的 Content-Disposition，并为旧客户端提供 ASCII 文件名。"""
    kind = "inline" if media_type in _INLINE_TYPES else "attachment"
    stem, dot, suffix = file_name.rpartition(".")
    fallback = "".join(
        char if char.isascii() and char.isprintable() and char not in '"\\' else "_"
        for char in (stem or file_name)
    )
    fallback = f"{fallback}{dot}{suffix}" if stem else fallback
    return f"{kind}; filename=\"{fallback}\"; filename*=UTF-8''{quote(file_name)}"


def job_response(row: Row) -> JobResponse:
    job, document_id, file_name, title = row
    return JobResponse(
        id=job.id,
        version_id=job.version_id,
        document_id=document_id,
        document_title=title,
        file_name=file_name,
        kind=job.kind,
        ingest=job.ingest,
        status=job.status,
        stage=job.stage,
        progress_current=job.progress_current,
        progress_total=job.progress_total,
        attempts=job.attempts,
        max_attempts=job.max_attempts,
        run_after=job.run_after,
        error_code=job.error_code,
        error_message=job.error_message,
        created_by=job.created_by,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        updated_at=job.updated_at,
    )


def version_response(
    version: DocumentVersion, latest_job: Row | None
) -> VersionResponse:
    data = {
        name: getattr(version, name)
        for name in VersionResponse.model_fields
        if name not in ("has_pdf", "latest_job")
    }
    return VersionResponse(
        **data,
        has_pdf=version.file_type == "pdf" or version.pdf_object_key is not None,
        latest_job=job_response(latest_job) if latest_job is not None else None,
    )


def document_response(
    document: Document, versions: list[DocumentVersion]
) -> DocumentResponse:
    """versions 为该制度的全部版本，按创建时间从新到旧。"""
    published = next((v for v in versions if v.status == "published"), None)
    return DocumentResponse(
        id=document.id,
        external_id=document.external_id,
        title=document.title,
        issuer=document.issuer,
        category=document.category,
        tags=list(document.tags or ()),
        version_count=len(versions),
        published_version=VersionBrief.model_validate(published) if published else None,
        latest_version=VersionBrief.model_validate(versions[0]) if versions else None,
        created_by=document.created_by,
        created_at=document.created_at,
        updated_at=document.updated_at,
    )


async def list_documents(
    session: AsyncSession,
    *,
    page: int,
    page_size: int,
    q: str | None,
    published: bool | None,
    category: str | None = None,
    tag: str | None = None,
) -> DocumentPage:
    async with session.begin():
        documents, total = await DocumentRepository(session).list_page(
            q=q,
            published=published,
            category=category,
            tag=tag,
            offset=(page - 1) * page_size,
            limit=page_size,
        )
        versions = await DocumentVersionRepository(session).list_for_documents(
            [document.id for document in documents]
        )
        by_document: dict[UUID, list[DocumentVersion]] = {}
        for version in versions:
            by_document.setdefault(version.document_id, []).append(version)
        return DocumentPage(
            items=[
                document_response(document, by_document.get(document.id, []))
                for document in documents
            ],
            total=total,
            page=page,
            page_size=page_size,
        )


async def _document_with_versions(
    session: AsyncSession, document_id: UUID, *, for_update: bool = False
) -> tuple[Document, list[DocumentVersion]]:
    document = await DocumentRepository(session).get(document_id, for_update=for_update)
    if document is None:
        raise document_not_found()
    versions = await DocumentVersionRepository(session).list_for_document(document_id)
    return document, versions


async def get_document(document_id: UUID, session: AsyncSession) -> DocumentResponse:
    async with session.begin():
        return document_response(*await _document_with_versions(session, document_id))


async def update_document(
    document_id: UUID, payload: DocumentUpdate, session: AsyncSession
) -> DocumentResponse:
    async with session.begin():
        document, versions = await _document_with_versions(
            session, document_id, for_update=True
        )
        for name in payload.model_fields_set:
            setattr(document, name, getattr(payload, name))
        document.updated_at = utc_now()
        await session.flush()
        return document_response(document, versions)


async def list_versions(
    document_id: UUID, session: AsyncSession
) -> list[VersionResponse]:
    async with session.begin():
        _, versions = await _document_with_versions(session, document_id)
        jobs = ImportJobRepository(session)
        return [
            version_response(version, await jobs.latest_for_version(version.id))
            for version in versions
        ]


async def get_version(version_id: UUID, session: AsyncSession) -> VersionResponse:
    async with session.begin():
        version = await DocumentVersionRepository(session).get(version_id)
        if version is None:
            raise version_not_found()
        latest = await ImportJobRepository(session).latest_for_version(version_id)
        return version_response(version, latest)


async def update_version(
    version_id: UUID, payload: VersionUpdate, session: AsyncSession
) -> VersionResponse:
    """只修改登记信息，不影响切片；发布后仍可更正信息与标记时效。"""
    async with session.begin():
        version = await DocumentVersionRepository(session).get(
            version_id, for_update=True
        )
        if version is None:
            raise version_not_found()
        for name in payload.model_fields_set:
            setattr(version, name, getattr(payload, name))
        await session.flush()
        latest = await ImportJobRepository(session).latest_for_version(version_id)
        return version_response(version, latest)


async def list_chunks(
    version_id: UUID, session: AsyncSession, *, page: int, page_size: int
) -> ChunkPage:
    async with session.begin():
        if await DocumentVersionRepository(session).get(version_id) is None:
            raise version_not_found()
        chunks, total = await DocumentChunkRepository(session).list_for_version(
            version_id, offset=(page - 1) * page_size, limit=page_size
        )
        return ChunkPage(
            items=[ChunkResponse.model_validate(chunk) for chunk in chunks],
            total=total,
            page=page,
            page_size=page_size,
        )


async def list_jobs(
    session: AsyncSession,
    *,
    page: int,
    page_size: int,
    status: str | None,
    kind: str | None,
    document_id: UUID | None,
    version_id: UUID | None,
) -> JobPage:
    async with session.begin():
        rows, total = await ImportJobRepository(session).list_page(
            status=status,
            kind=kind,
            document_id=document_id,
            version_id=version_id,
            offset=(page - 1) * page_size,
            limit=page_size,
        )
        return JobPage(
            items=[job_response(row) for row in rows],
            total=total,
            page=page,
            page_size=page_size,
        )


async def get_job(job_id: UUID, session: AsyncSession) -> JobResponse:
    async with session.begin():
        row = await ImportJobRepository(session).get_with_context(job_id)
        if row is None:
            raise job_not_found()
        return job_response(row)


@dataclass(frozen=True, slots=True)
class FileDownload:
    chunks: Iterator[bytes]
    size: int
    media_type: str
    disposition: str


async def open_version_file(
    version_id: UUID,
    variant: Literal["original", "pdf"],
    session: AsyncSession,
    storage: ObjectStorage,
) -> FileDownload:
    """original 为上传的原件；pdf 为可预览的 PDF（原件或 .doc 等的转换结果），页码以它为准。"""
    async with session.begin():
        version = await DocumentVersionRepository(session).get(version_id)
        if version is None:
            raise version_not_found()
        file_name = version.file_name
        key = version.object_key
        file_type = version.file_type
        if variant == "pdf" and file_type != "pdf":
            if version.pdf_object_key is None:
                raise file_not_available()
            key = version.pdf_object_key
            file_type = "pdf"
            file_name = f"{file_name.rpartition('.')[0] or file_name}.pdf"
    try:
        info, chunks = await run_in_threadpool(storage.open_object, key)
    except FileNotFoundError as error:
        raise file_missing() from error
    media_type = media_type_for(file_type)
    return FileDownload(
        chunks=chunks,
        size=info.size,
        media_type=media_type,
        disposition=content_disposition(file_name, media_type),
    )


_VALIDITY_ORDER = {
    "effective": 0,
    "not_yet_effective": 1,
    "unknown": 2,
    "amended": 3,
    "repealed": 4,
}


def _library_document_summary(
    document: Document, version: DocumentVersion
) -> LibraryDocumentSummary:
    return LibraryDocumentSummary(
        id=document.id,
        title=document.title,
        issuer=document.issuer,
        category=document.category,
        tags=list(document.tags or ()),
        version=LibraryPublishedVersion(
            id=version.id,
            document_number=version.document_number,
            version_note=version.version_note,
            published_date=version.published_date,
            effective_date=version.effective_date,
            validity_status=version.validity_status,
            validity_checked_at=version.validity_checked_at,
            source_url=version.source_url,
            page_count=version.page_count,
            chunk_count=version.chunk_count,
            published_at=version.published_at,
        ),
        updated_at=document.updated_at,
    )


async def get_library_facets(session: AsyncSession) -> LibraryFacetsResponse:
    async with session.begin():
        rows = await DocumentVersionRepository(session).list_published_with_documents()
    category_counts: Counter[str] = Counter()
    tag_counts: Counter[str] = Counter()
    validity_counts: Counter[str] = Counter()
    total_chunks = 0
    for document, version in rows:
        total_chunks += int(version.chunk_count or 0)
        if document.category:
            category_counts[document.category] += 1
        for tag in document.tags or ():
            tag_counts[tag] += 1
        validity_counts[version.validity_status] += 1
    return LibraryFacetsResponse(
        total_documents=len(rows),
        total_chunks=total_chunks,
        categories=[
            FacetCount(name=name, count=count)
            for name, count in sorted(
                category_counts.items(), key=lambda pair: (-pair[1], pair[0])
            )
        ],
        tags=[
            FacetCount(name=name, count=count)
            for name, count in sorted(
                tag_counts.items(), key=lambda pair: (-pair[1], pair[0])
            )
        ],
        validity_counts=dict(validity_counts),
    )


async def list_library_documents(
    session: AsyncSession,
    *,
    page: int,
    page_size: int,
    q: str | None = None,
    category: str | None = None,
    tag: str | None = None,
    validity_status: ValidityStatus | None = None,
    include_repealed: bool = True,
) -> LibraryDocumentPage:
    async with session.begin():
        rows = await DocumentVersionRepository(session).list_published_with_documents()
    keyword = q.strip().casefold() if q else ""
    tag_needle = tag.strip().casefold() if tag else ""
    filtered: list[tuple[Document, DocumentVersion]] = []
    for document, version in rows:
        if validity_status is not None and version.validity_status != validity_status:
            continue
        if (
            validity_status is None
            and not include_repealed
            and version.validity_status == "repealed"
        ):
            continue
        if category and document.category != category:
            continue
        if tag_needle and not any(
            item.casefold() == tag_needle for item in (document.tags or ())
        ):
            continue
        if keyword:
            haystack = " ".join(
                part
                for part in (
                    document.title,
                    document.issuer or "",
                    document.category or "",
                    version.document_number or "",
                    version.version_note or "",
                    " ".join(document.tags or ()),
                )
                if part
            ).casefold()
            if keyword not in haystack:
                continue
        filtered.append((document, version))
    # 现行有效制度优先展示，同状态按更新时间倒序
    filtered.sort(
        key=lambda pair: (
            _VALIDITY_ORDER.get(pair[1].validity_status, 9),
            -pair[0].updated_at.timestamp(),
            str(pair[0].id),
        )
    )
    offset = (page - 1) * page_size
    page_rows = filtered[offset : offset + page_size]
    return LibraryDocumentPage(
        items=[_library_document_summary(doc, ver) for doc, ver in page_rows],
        total=len(filtered),
        page=page,
        page_size=page_size,
    )


async def get_library_document(
    document_id: UUID, session: AsyncSession
) -> LibraryDocumentSummary:
    async with session.begin():
        document = await DocumentRepository(session).get(document_id)
        if document is None:
            raise document_not_found()
        version = await DocumentVersionRepository(session).get_published(document_id)
        if version is None:
            raise document_not_found()
        return _library_document_summary(document, version)


async def list_library_chunks(
    document_id: UUID,
    session: AsyncSession,
    *,
    page: int,
    page_size: int,
    q: str | None = None,
) -> ChunkPage:
    async with session.begin():
        version = await DocumentVersionRepository(session).get_published(document_id)
        if version is None:
            raise document_not_found()
        chunks, total = await DocumentChunkRepository(session).list_for_version(
            version.id,
            offset=(page - 1) * page_size,
            limit=page_size,
            q=q.strip() if q else None,
        )
        return ChunkPage(
            items=[ChunkResponse.model_validate(chunk) for chunk in chunks],
            total=total,
            page=page,
            page_size=page_size,
        )
