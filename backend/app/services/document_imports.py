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
from app.rag.parsing import SUPPORTED_TYPES, ParseOptions
from app.rag.parsing.errors import file_too_large, invalid_document, unsupported_type
from app.rag.parsing.options import MIB
from app.repositories.documents import DocumentRepository, DocumentVersionRepository
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
from app.storage.keys import blocks_key, original_key

logger = logging.getLogger(__name__)

MAX_UPLOAD_BYTES = ParseOptions().max_file_bytes
_FILE_NAME_LIMIT = 255
# 解析结束或失败、且没有进行中任务的版本可以删除或重新解析；已发布与已归档的版本保留
_REPARSABLE = frozenset({"parsed", "failed"})
_DELETABLE = frozenset({"uploaded", "parsed", "failed"})


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
        if version.status != "failed":
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
        version.status = "uploaded"
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
        version = await _lock_idle_version(session, version_id)
        if version.status not in _REPARSABLE:
            raise version_state_conflict("只能重新解析已解析或解析失败的版本")
        if options is not None:
            version.parse_options = options.stored()
        version.status = "uploaded"
        job = ImportJob(
            version_id=version_id, kind="parse", run_after=utc_now(), created_by=user.id
        )
        jobs = ImportJobRepository(session)
        await jobs.add(job)
        return job_response(await jobs.get_with_context(job.id))


async def delete_version(
    version_id: UUID, session: AsyncSession, storage: ObjectStorage
) -> None:
    """删除未发布的版本及其切片、任务和对象；已发布或已归档的版本供历史引用回溯，不能删除。"""
    async with session.begin():
        version = await _lock_idle_version(session, version_id)
        if version.status not in _DELETABLE:
            raise version_state_conflict("已发布或已归档的版本不能删除")
        keys = [
            version.object_key,
            version.pdf_object_key,
            blocks_key(version.document_id, version.id),
        ]
        # 切片与任务由外键级联删除
        await session.delete(version)
    # 数据库已提交后再删对象；失败只留下孤儿对象，不影响数据一致
    for key in keys:
        if key is not None:
            await run_in_threadpool(_delete_quietly, storage, key)


async def _lock_idle_version(
    session: AsyncSession, version_id: UUID
) -> DocumentVersion:
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


def _delete_quietly(storage: ObjectStorage, key: str) -> None:
    try:
        storage.delete(key)
    except AppException:
        logger.warning("storage_cleanup_failed: %s", key)
