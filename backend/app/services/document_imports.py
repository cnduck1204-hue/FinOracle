"""文档导入：登记上传的原件并创建解析任务，以及重试、重新解析与删除未发布版本。

原件先写入对象存储，再在一个事务中登记制度、版本与任务；数据库写入失败时删除刚上传的
对象。解析由 worker 异步完成，接口立即返回任务。
"""

import hashlib
import logging
import tempfile
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import UploadFile
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.core.exceptions import AppException
from app.db.base import utc_now
from app.models.document import Document, DocumentVersion
from app.models.import_job import ImportJob
from app.models.user import User
from app.rag.indexing import VectorStore
from app.rag.parsing import SUPPORTED_TYPES, ParseOptions
from app.rag.parsing.errors import file_too_large, invalid_document, unsupported_type
from app.rag.parsing.options import MIB
from app.rag.pipelines import PipelineRegistry
from app.repositories.documents import (
    DocumentRepository,
    DocumentVersionRepository,
    VersionIndexRepository,
)
from app.repositories.import_jobs import ImportJobRepository
from app.schemas.document import ParseOptionsInput
from app.schemas.ingestion import ImportForm, JobResponse
from app.services.document_errors import (
    document_not_found,
    duplicate_version,
    import_in_progress,
    job_not_found,
    job_not_retryable,
    version_not_found,
    version_state_conflict,
)
from app.services.document_library import job_response, media_type_for
from app.storage import ObjectStorage
from app.storage.keys import blocks_key, original_key, version_prefix

logger = logging.getLogger(__name__)

MAX_UPLOAD_BYTES = ParseOptions().max_file_bytes
_FILE_NAME_LIMIT = 255
# 没有进行中任务的草稿版本可以重新解析或删除；已发布与已归档的版本供历史引用回溯，保留
_REPARSABLE = frozenset({"parsed", "failed", "ready"})
_DELETABLE = frozenset({"uploaded", "parsed", "failed", "ready"})
# 可批准向量化的状态：解析完成，或下架后需要重新上线
_APPROVABLE = frozenset({"parsed", "archived"})


def clean_file_name(name: str | None) -> str:
    """去掉客户端附带的目录，过长时截断主名并保留扩展名。"""
    base = (name or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    if len(base) <= _FILE_NAME_LIMIT:
        return base
    stem, dot, suffix = base.rpartition(".")
    if not stem or len(suffix) > 16:
        return base[:_FILE_NAME_LIMIT]
    return stem[: _FILE_NAME_LIMIT - len(suffix) - 1] + dot + suffix


async def create_import(
    form: ImportForm, user: User, session: AsyncSession, storage: ObjectStorage
) -> JobResponse:
    file_name = clean_file_name(form.file.filename)
    file_type = Path(file_name).suffix.lower().lstrip(".")
    if file_type not in SUPPORTED_TYPES:
        raise unsupported_type(file_type)
    if form.file.size is not None and form.file.size > MAX_UPLOAD_BYTES:
        raise file_too_large(MAX_UPLOAD_BYTES)
    if form.document_id is not None:
        async with session.begin():
            if await DocumentRepository(session).get(form.document_id) is None:
                raise document_not_found()

    document_id = form.document_id or uuid4()
    version_id = uuid4()
    key = original_key(document_id, version_id, file_type)
    with tempfile.TemporaryDirectory(prefix="finoracle-import-") as workdir:
        path = Path(workdir) / "upload"
        sha256, size = await run_in_threadpool(_save_upload, form.file, path)
        if form.document_id is not None:
            async with session.begin():
                versions = DocumentVersionRepository(session)
                if await versions.get_by_sha256(form.document_id, sha256) is not None:
                    raise duplicate_version()
        await run_in_threadpool(
            storage.put_file, key, path, content_type=media_type_for(file_type)
        )

    try:
        async with session.begin():
            if form.document_id is None:
                document = Document(
                    id=document_id,
                    title=form.title,
                    issuer=form.issuer,
                    created_by=user.id,
                )
                await DocumentRepository(session).add(document)
            else:
                document = await DocumentRepository(session).get(
                    document_id, for_update=True
                )
                if document is None:
                    raise document_not_found()
                # 新版本让制度排到列表前面
                document.updated_at = utc_now()
            version = DocumentVersion(
                id=version_id,
                document_id=document_id,
                file_name=file_name,
                file_type=file_type,
                size_bytes=size,
                sha256=sha256,
                object_key=key,
                parse_options=form.stored(),
                created_by=user.id,
                **form.metadata(),
            )
            await DocumentVersionRepository(session).add(version)
            job = ImportJob(
                version_id=version_id,
                kind="parse",
                run_after=utc_now(),
                created_by=user.id,
            )
            await ImportJobRepository(session).add(job)
            return job_response((job, document_id, file_name, document.title))
    except Exception as error:
        # 登记失败时删除刚上传的原件；同一文件被并发登记时唯一约束报冲突
        await run_in_threadpool(_delete_quietly, storage, key)
        if isinstance(error, IntegrityError):
            raise duplicate_version() from error
        raise


async def retry_job(job_id: UUID, session: AsyncSession) -> JobResponse:
    """把失败的任务重新排队，沿用同一任务并重置尝试次数。"""
    async with session.begin():
        jobs = ImportJobRepository(session)
        # 与 worker 相同，先锁任务再锁版本
        job = await jobs.get(job_id, for_update=True)
        if job is None:
            raise job_not_found()
        if job.status != "failed":
            raise job_not_retryable()
        version = await DocumentVersionRepository(session).get(
            job.version_id, for_update=True
        )
        if await jobs.get_active_for_version(job.version_id) is not None:
            raise import_in_progress()
        if job.kind == "pipeline":
            # 附加方案的失败不改变版本状态，只看该方案自己的索引状态
            index = await VersionIndexRepository(session).get(
                job.version_id, job.ingest or "", for_update=True
            )
            if index is None or index.status != "failed":
                raise version_state_conflict("该方案的索引已不在失败状态")
            index.status = "building"
        elif version.status != "failed":
            raise version_state_conflict("版本已不在失败状态，请使用重新解析")
        job.status = "queued"
        job.attempts = 0
        job.run_after = utc_now()
        job.stage = None
        job.progress_current = 0
        job.progress_total = None
        job.error_code = None
        job.error_message = None
        job.started_at = None
        job.finished_at = None
        if job.kind != "pipeline":
            version.status = "uploaded" if job.kind == "parse" else "indexing"
        await session.flush()
        return job_response(await jobs.get_with_context(job_id))


async def reparse_version(
    version_id: UUID,
    options: ParseOptionsInput | None,
    user: User,
    session: AsyncSession,
) -> JobResponse:
    """用新的（或原来的）解析参数创建解析任务；完成后替换原有切片。"""
    async with session.begin():
        version = await lock_idle_version(session, version_id)
        if version.status not in _REPARSABLE:
            raise version_state_conflict("只能重新解析尚未发布的版本")
        if options is not None:
            version.parse_options = options.stored()
        version.status = "uploaded"
        job = ImportJob(
            version_id=version_id, kind="parse", run_after=utc_now(), created_by=user.id
        )
        jobs = ImportJobRepository(session)
        await jobs.add(job)
        return job_response(await jobs.get_with_context(job.id))


async def approve_version(
    version_id: UUID, user: User, session: AsyncSession
) -> JobResponse:
    """批准向量化：创建索引任务，完成后版本进入 ready（待发布）。

    默认配置下 worker 自动批准完整解析的版本；需要人工批准的是含待 OCR 图片的版本、
    关闭自动批准时的版本，以及下架后要重新上线的版本。
    """
    async with session.begin():
        version = await lock_idle_version(session, version_id)
        if version.status not in _APPROVABLE:
            raise version_state_conflict("只能批准已解析或已下架的版本")
        version.status = "indexing"
        job = ImportJob(
            version_id=version_id, kind="index", run_after=utc_now(), created_by=user.id
        )
        jobs = ImportJobRepository(session)
        await jobs.add(job)
        return job_response(await jobs.get_with_context(job.id))


async def delete_version(
    version_id: UUID,
    session: AsyncSession,
    storage: ObjectStorage,
    store: VectorStore,
    pipelines: PipelineRegistry | None = None,
) -> None:
    """删除未发布的版本及其切片、任务、对象与向量；已发布或已归档的版本供历史引用回溯，不能删除。"""
    async with session.begin():
        version = await lock_idle_version(session, version_id)
        if version.status not in _DELETABLE:
            raise version_state_conflict("已发布或已归档的版本不能删除")
        keys = [
            version.object_key,
            version.pdf_object_key,
            blocks_key(version.document_id, version.id),
        ]
        # 外键级联会删除索引记录，先取出需要清理的附加方案
        extra = await VersionIndexRepository(session).list_for_version(version_id)
        extra_ingests = [index.ingest for index in extra]
        pages_prefix = f"{version_prefix(version.document_id, version.id)}/pages/"
        # 切片与任务由外键级联删除
        await session.delete(version)
    # 数据库已提交后再删对象与向量；失败只留下孤儿数据，检索只读已发布版本，不受影响
    for key in keys:
        if key is not None:
            await run_in_threadpool(_delete_quietly, storage, key)
    await run_in_threadpool(delete_vectors_quietly, store, version_id)
    if extra_ingests:
        await run_in_threadpool(_delete_prefix_quietly, storage, pages_prefix)
    if pipelines is not None:
        for name in extra_ingests:
            await run_in_threadpool(pipelines.ingest(name).delete_quietly, version_id)


def delete_vectors_quietly(store: VectorStore, version_id: UUID) -> None:
    try:
        store.delete_by_version(version_id)
    except AppException:
        logger.warning("vector_cleanup_failed: %s", version_id)


async def lock_idle_version(session: AsyncSession, version_id: UUID) -> DocumentVersion:
    """锁定版本并确认没有进行中的任务。"""
    version = await DocumentVersionRepository(session).get(version_id, for_update=True)
    if version is None:
        raise version_not_found()
    if (
        await ImportJobRepository(session).get_active_for_version(version_id)
        is not None
    ):
        raise import_in_progress()
    return version


def _save_upload(upload: UploadFile, path: Path) -> tuple[str, int]:
    """复制上传内容并计算 SHA-256；超过上限或为空时拒绝。"""
    digest = hashlib.sha256()
    size = 0
    upload.file.seek(0)
    with path.open("wb") as output:
        while block := upload.file.read(MIB):
            size += len(block)
            if size > MAX_UPLOAD_BYTES:
                raise file_too_large(MAX_UPLOAD_BYTES)
            digest.update(block)
            output.write(block)
    if size == 0:
        raise invalid_document("文件为空")
    return digest.hexdigest(), size


def _delete_prefix_quietly(storage: ObjectStorage, prefix: str) -> None:
    try:
        storage.delete_prefix(prefix)
    except AppException:
        logger.warning("storage_cleanup_failed: %s", prefix)


def _delete_quietly(storage: ObjectStorage, key: str) -> None:
    try:
        storage.delete(key)
    except AppException:
        logger.warning("storage_cleanup_failed: %s", key)
