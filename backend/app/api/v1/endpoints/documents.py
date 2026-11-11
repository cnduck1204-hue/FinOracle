from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Form, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_admin
from app.core.config import ParsingSettings, get_parsing_settings
from app.db.session import get_db
from app.rag.parsing import ParseResult
from app.schemas.document import ParseUploadForm
from app.schemas.ingestion import (
    DocumentPage,
    DocumentResponse,
    DocumentUpdate,
    VersionResponse,
)
from app.services import document_library, document_parsing

router = APIRouter(dependencies=[Depends(require_admin)])


@router.post(
    "/parse",
    response_model=ParseResult,
    summary="上传文档并返回解析结果（预览，不入库）",
)
async def parse_document(
    # 显式声明 multipart，接口文档导入 Apifox 等工具后才能直接选择文件
    form: Annotated[ParseUploadForm, Form(media_type="multipart/form-data")],
    response: Response,
    settings: Annotated[ParsingSettings, Depends(get_parsing_settings)],
) -> ParseResult:
    response.headers["Cache-Control"] = "no-store"
    return await document_parsing.parse_upload(form.file, form, settings)


@router.get("", response_model=DocumentPage, summary="制度列表")
async def list_documents(
    response: Response,
    session: Annotated[AsyncSession, Depends(get_db)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    q: Annotated[
        str | None,
        Query(min_length=1, max_length=255, description="标题、发文机构或外部 ID"),
    ] = None,
    published: Annotated[
        bool | None, Query(description="true 只看有发布版本的制度，false 只看没有的")
    ] = None,
) -> DocumentPage:
    response.headers["Cache-Control"] = "no-store"
    return await document_library.list_documents(
        session, page=page, page_size=page_size, q=q, published=published
    )


@router.get("/{document_id}", response_model=DocumentResponse, summary="制度详情")
async def read_document(
    document_id: UUID,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> DocumentResponse:
    response.headers["Cache-Control"] = "no-store"
    return await document_library.get_document(document_id, session)


@router.patch("/{document_id}", response_model=DocumentResponse, summary="修改制度信息")
async def update_document(
    document_id: UUID,
    payload: DocumentUpdate,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> DocumentResponse:
    response.headers["Cache-Control"] = "no-store"
    return await document_library.update_document(document_id, payload, session)


@router.get(
    "/{document_id}/versions",
    response_model=list[VersionResponse],
    summary="制度的全部版本（从新到旧）",
)
async def list_versions(
    document_id: UUID,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> list[VersionResponse]:
    response.headers["Cache-Control"] = "no-store"
    return await document_library.list_versions(document_id, session)
