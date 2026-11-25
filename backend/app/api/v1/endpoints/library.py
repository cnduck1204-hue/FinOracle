"""面向登录用户的制度库浏览与条文阅读接口。"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_current_user
from app.db.session import get_db
from app.schemas.ingestion import ChunkPage, ValidityStatus
from app.schemas.library import (
    LibraryDocumentPage,
    LibraryDocumentSummary,
    LibraryFacetsResponse,
)
from app.services import document_library

router = APIRouter(dependencies=[Depends(require_current_user)])


@router.get(
    "/facets",
    response_model=LibraryFacetsResponse,
    summary="制度库分类、标签与时效统计",
)
async def get_facets(
    response: Response,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> LibraryFacetsResponse:
    response.headers["Cache-Control"] = "no-store"
    return await document_library.get_library_facets(session)


@router.get("", response_model=LibraryDocumentPage, summary="浏览已发布的金融制度")
async def list_library(
    response: Response,
    session: Annotated[AsyncSession, Depends(get_db)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    q: Annotated[str | None, Query(min_length=1, max_length=255)] = None,
    category: Annotated[str | None, Query(min_length=1, max_length=64)] = None,
    tag: Annotated[str | None, Query(min_length=1, max_length=32)] = None,
    validity_status: ValidityStatus | None = None,
    include_repealed: bool = True,
) -> LibraryDocumentPage:
    response.headers["Cache-Control"] = "no-store"
    return await document_library.list_library_documents(
        session,
        page=page,
        page_size=page_size,
        q=q,
        category=category,
        tag=tag,
        validity_status=validity_status,
        include_repealed=include_repealed,
    )


@router.get(
    "/{document_id}",
    response_model=LibraryDocumentSummary,
    summary="查看已发布制度详情",
)
async def read_library_document(
    document_id: UUID,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> LibraryDocumentSummary:
    response.headers["Cache-Control"] = "no-store"
    return await document_library.get_library_document(document_id, session)


@router.get(
    "/{document_id}/chunks",
    response_model=ChunkPage,
    summary="分页浏览已发布制度的条文原文",
)
async def list_library_chunks(
    document_id: UUID,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_db)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    q: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
) -> ChunkPage:
    response.headers["Cache-Control"] = "no-store"
    return await document_library.list_library_chunks(
        document_id, session, page=page, page_size=page_size, q=q
    )
