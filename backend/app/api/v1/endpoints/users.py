from typing import Annotated

from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_current_user
from app.db.session import get_db
from app.models.user import User
from app.schemas.profile import ProfileUpdateRequest
from app.schemas.user import UserResponse
from app.services.profile import update_profile

router = APIRouter()


@router.get(
    "/me",
    response_model=UserResponse,
)
async def read_current_user(
    response: Response,
    user: Annotated[User, Depends(require_current_user)],
) -> UserResponse:
    response.headers["Cache-Control"] = "no-store"

    return UserResponse.model_validate(user)


@router.patch("/me", response_model=UserResponse)
async def update_current_user(
    payload: ProfileUpdateRequest,
    response: Response,
    user: Annotated[User, Depends(require_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> UserResponse:
    updated_user = await update_profile(payload, user.id, session)
    response.headers["Cache-Control"] = "no-store"
    return UserResponse.model_validate(updated_user)
