from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Form, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_admin
from app.db.session import get_db
from app.models.user import User
from app.schemas.ingestion import ImportForm, JobKind, JobPage, JobResponse, JobStatus
from app.services import document_imports, document_library
from app.storage import ObjectStorage, get_storage

router = APIRouter(dependencies=[Depends(require_admin)])


@router.post(
    "",
    response_model=JobResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="上传文档并创建解析任务",
    description="原件写入对象存储后立即返回任务，由后台 worker 解析；用任务接口查看进度。",
)
async def create_import(
    form: Annotated[ImportForm, Form(media_type="multipart/form-data")],
    response: Response,
    user: Annotated[User, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_db)],
    storage: Annotated[ObjectStorage, Depends(get_storage)],
) -> JobResponse:
    response.headers["Cache-Control"] = "no-store"
    return await document_imports.create_import(form, user, session, storage)


@router.get("", response_model=JobPage, summary="导入任务列表（从新到旧）")
async def list_jobs(
    response: Response,
    session: Annotated[AsyncSession, Depends(get_db)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    status: JobStatus | None = None,
    kind: JobKind | None = None,
    document_id: UUID | None = None,
    version_id: UUID | None = None,
) -> JobPage:
    response.headers["Cache-Control"] = "no-store"
    return await document_library.list_jobs(
        session,
        page=page,
        page_size=page_size,
        status=status,
        kind=kind,
        document_id=document_id,
        version_id=version_id,
    )


@router.get("/{job_id}", response_model=JobResponse, summary="任务详情与进度")
async def read_job(
    job_id: UUID,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> JobResponse:
    response.headers["Cache-Control"] = "no-store"
    return await document_library.get_job(job_id, session)


@router.post(
    "/{job_id}/retry",
    response_model=JobResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="重新执行失败的任务",
)
async def retry_job(
    job_id: UUID,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> JobResponse:
    response.headers["Cache-Control"] = "no-store"
    return await document_imports.retry_job(job_id, session)
