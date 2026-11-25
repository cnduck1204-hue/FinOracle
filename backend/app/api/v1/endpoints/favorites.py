"""用户收藏夹与收藏项接口。"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_current_user
from app.db.session import get_db
from app.models.favorite import FavoriteKind
from app.models.user import User
from app.schemas.favorite import (
    FavoriteBatchDelete,
    FavoriteBatchMove,
    FavoriteCreate,
    FavoriteFolderCreate,
    FavoriteFolderSummary,
    FavoriteFolderUpdate,
    FavoriteItem,
    FavoriteListResponse,
    FavoriteLookupResponse,
    FavoriteScope,
    FavoriteTreeResponse,
    FavoriteUpdate,
)
from app.services import favorites as favorites_service

router = APIRouter(dependencies=[Depends(require_current_user)])


@router.get(
    "/tree", response_model=FavoriteTreeResponse, summary="获取本人收藏夹文件夹树"
)
async def get_tree(
    response: Response,
    user: Annotated[User, Depends(require_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> FavoriteTreeResponse:
    response.headers["Cache-Control"] = "no-store"
    return await favorites_service.get_tree(session, user.id)


@router.get(
    "/lookup", response_model=FavoriteLookupResponse, summary="按来源键查询收藏状态"
)
async def lookup_sources(
    response: Response,
    user: Annotated[User, Depends(require_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
    keys: Annotated[str, Query(max_length=4000)] = "",
) -> FavoriteLookupResponse:
    response.headers["Cache-Control"] = "no-store"
    key_list = [part for part in keys.split(",") if part.strip()]
    return await favorites_service.lookup_sources(session, user.id, key_list)


@router.get("", response_model=FavoriteListResponse, summary="列出文件夹或搜索本人收藏")
async def list_favorites(
    response: Response,
    user: Annotated[User, Depends(require_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
    folder_id: UUID | None = None,
    scope: FavoriteScope = "root",
    q: Annotated[str, Query(max_length=100)] = "",
    kind: FavoriteKind | None = None,
) -> FavoriteListResponse:
    response.headers["Cache-Control"] = "no-store"
    return await favorites_service.list_contents(
        session,
        user.id,
        folder_id=folder_id,
        scope=scope,
        query=q,
        kind=kind,
    )


@router.post(
    "/folders",
    status_code=201,
    response_model=FavoriteFolderSummary,
    summary="新建收藏文件夹",
)
async def create_folder(
    payload: FavoriteFolderCreate,
    response: Response,
    user: Annotated[User, Depends(require_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> FavoriteFolderSummary:
    response.headers["Cache-Control"] = "no-store"
    return await favorites_service.create_folder(session, user.id, payload)


@router.patch(
    "/folders/{folder_id}",
    response_model=FavoriteFolderSummary,
    summary="重命名或移动收藏文件夹",
)
async def update_folder(
    folder_id: UUID,
    payload: FavoriteFolderUpdate,
    response: Response,
    user: Annotated[User, Depends(require_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> FavoriteFolderSummary:
    response.headers["Cache-Control"] = "no-store"
    return await favorites_service.update_folder(session, user.id, folder_id, payload)


@router.delete(
    "/folders/{folder_id}", status_code=204, summary="删除收藏文件夹及其内容"
)
async def delete_folder(
    folder_id: UUID,
    user: Annotated[User, Depends(require_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> Response:
    await favorites_service.delete_folder(session, user.id, folder_id)
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


@router.post(
    "",
    status_code=201,
    response_model=FavoriteItem,
    summary="新增收藏项（问答、条文或链接）",
)
async def create_favorite(
    payload: FavoriteCreate,
    response: Response,
    user: Annotated[User, Depends(require_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> FavoriteItem:
    response.headers["Cache-Control"] = "no-store"
    return await favorites_service.create_favorite(session, user.id, payload)


@router.patch(
    "/{favorite_id}",
    response_model=FavoriteItem,
    summary="编辑或移动收藏项",
)
async def update_favorite(
    favorite_id: UUID,
    payload: FavoriteUpdate,
    response: Response,
    user: Annotated[User, Depends(require_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> FavoriteItem:
    response.headers["Cache-Control"] = "no-store"
    return await favorites_service.update_favorite(
        session, user.id, favorite_id, payload
    )


@router.delete("/{favorite_id}", status_code=204, summary="删除收藏项")
async def delete_favorite(
    favorite_id: UUID,
    user: Annotated[User, Depends(require_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> Response:
    await favorites_service.delete_favorite(session, user.id, favorite_id)
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


@router.post("/batch-delete", status_code=204, summary="批量删除文件夹与收藏项")
async def batch_delete(
    payload: FavoriteBatchDelete,
    user: Annotated[User, Depends(require_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> Response:
    await favorites_service.batch_delete(session, user.id, payload)
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


@router.post("/batch-move", status_code=204, summary="批量移动文件夹与收藏项")
async def batch_move(
    payload: FavoriteBatchMove,
    user: Annotated[User, Depends(require_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> Response:
    await favorites_service.batch_move(session, user.id, payload)
    return Response(status_code=204, headers={"Cache-Control": "no-store"})
