from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_admin
from app.db.session import get_db
from app.models.user import User
from app.schemas.admin import UserPage, UserStatusRequest
from app.schemas.user import UserResponse
from app.services import admin

router = APIRouter(dependencies=[Depends(require_admin)])


@router.get("/users", response_model=UserPage)
async def list_users(
    response: Response,
    session: Annotated[AsyncSession, Depends(get_db)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    q: Annotated[str | None, Query(min_length=1, max_length=254)] = None,
    role: Literal["user", "admin"] | None = None,
    status: Literal["active", "disabled"] | None = None,
) -> UserPage:
    response.headers["Cache-Control"] = "no-store"
    return await admin.list_users(
        session, page=page, page_size=page_size, q=q, role=role, status=status
    )


@router.get("/users/{user_id}", response_model=UserResponse)
async def read_user(
    user_id: UUID,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    response.headers["Cache-Control"] = "no-store"
    return await admin.get_user(user_id, session)


@router.patch("/users/{user_id}/status", response_model=UserResponse)
async def update_user_status(
    user_id: UUID,
    payload: UserStatusRequest,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    response.headers["Cache-Control"] = "no-store"
    return await admin.set_user_status(user_id, payload.status, session)
