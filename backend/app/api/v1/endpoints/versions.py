from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Body, Depends, Query, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_admin
from app.db.session import get_db
from app.models.user import User
from app.rag.indexing import VectorStore, get_vector_store
from app.schemas.document import ParseOptionsInput
from app.schemas.ingestion import ChunkPage, JobResponse, VersionResponse, VersionUpdate
from app.services import document_imports, document_library, document_publishing
from app.storage import ObjectStorage, get_storage

router = APIRouter(dependencies=[Depends(require_admin)])


@router.get("/{version_id}", response_model=VersionResponse, summary="版本详情")
async def read_version(
    version_id: UUID,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> VersionResponse:
    response.headers["Cache-Control"] = "no-store"
    return await document_library.get_version(version_id, session)


@router.patch(
    "/{version_id}",
    response_model=VersionResponse,
    summary="修改版本登记信息",
    description="只修改文号、日期、时效与来源等信息，不影响切片；可为空的字段传 null 清除。",
)
async def update_version(
    version_id: UUID,
    payload: VersionUpdate,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> VersionResponse:
    response.headers["Cache-Control"] = "no-store"
    return await document_library.update_version(version_id, payload, session)


@router.get(
    "/{version_id}/chunks", response_model=ChunkPage, summary="版本切片（按顺序分页）"
)
async def list_chunks(
    version_id: UUID,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_db)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ChunkPage:
    response.headers["Cache-Control"] = "no-store"
    return await document_library.list_chunks(
        version_id, session, page=page, page_size=page_size
    )


@router.get(
    "/{version_id}/file",
    response_class=StreamingResponse,
    summary="读取原件或可预览的 PDF",
    description=(
        "variant=original 返回上传的原件；variant=pdf 返回 PDF（原件即 PDF，"
        "或 .doc 等转换后的 PDF），切片页码以它为准。PDF 与常见图片在浏览器中直接显示，"
        "其他格式以附件下载。"
    ),
)
async def read_file(
    version_id: UUID,
    session: Annotated[AsyncSession, Depends(get_db)],
    storage: Annotated[ObjectStorage, Depends(get_storage)],
    variant: Literal["original", "pdf"] = "original",
) -> StreamingResponse:
    download = await document_library.open_version_file(
        version_id, variant, session, storage
    )
    return StreamingResponse(
        download.chunks,
        # 直接写 Content-Type：Starlette 会给 text/* 补 charset=utf-8，而文本原件可能是 GB18030
        headers={
            "Content-Type": download.media_type,
            "Content-Length": str(download.size),
            "Content-Disposition": download.disposition,
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post(
    "/{version_id}/reparse",
    response_model=JobResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="重新解析版本",
    description="可选提交新的解析参数，不提交则沿用原参数；完成后替换原有切片。",
)
async def reparse_version(
    version_id: UUID,
    response: Response,
    user: Annotated[User, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_db)],
    options: Annotated[ParseOptionsInput | None, Body()] = None,
) -> JobResponse:
    response.headers["Cache-Control"] = "no-store"
    return await document_imports.reparse_version(version_id, options, user, session)


@router.post(
    "/{version_id}/approve",
    response_model=JobResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="批准向量化",
    description=(
        "为已解析或已下架的版本创建索引任务，完成后版本进入 ready。默认配置下完整解析的版本"
        "会自动批准，需要人工批准的是含待 OCR 图片的版本和下架后要重新上线的版本。"
    ),
)
async def approve_version(
    version_id: UUID,
    response: Response,
    user: Annotated[User, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> JobResponse:
    response.headers["Cache-Control"] = "no-store"
    return await document_imports.approve_version(version_id, user, session)


@router.post(
    "/{version_id}/publish",
    response_model=VersionResponse,
    summary="发布版本",
    description="发布 ready 状态的版本；同一制度原来的发布版本自动归档，不再进入检索。",
)
async def publish_version(
    version_id: UUID,
    response: Response,
    user: Annotated[User, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_db)],
    store: Annotated[VectorStore, Depends(get_vector_store)],
) -> VersionResponse:
    response.headers["Cache-Control"] = "no-store"
    return await document_publishing.publish_version(version_id, user, session, store)


@router.post(
    "/{version_id}/withdraw",
    response_model=VersionResponse,
    summary="下架版本",
    description="已发布版本改为归档，不再进入检索；切片与原件保留，可再次批准并发布。",
)
async def withdraw_version(
    version_id: UUID,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_db)],
    store: Annotated[VectorStore, Depends(get_vector_store)],
) -> VersionResponse:
    response.headers["Cache-Control"] = "no-store"
    return await document_publishing.withdraw_version(version_id, session, store)


@router.delete(
    "/{version_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="删除未发布的版本",
)
async def delete_version(
    version_id: UUID,
    session: Annotated[AsyncSession, Depends(get_db)],
    storage: Annotated[ObjectStorage, Depends(get_storage)],
    store: Annotated[VectorStore, Depends(get_vector_store)],
) -> Response:
    await document_imports.delete_version(version_id, session, storage, store)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
