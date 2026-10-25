from typing import Annotated

from fastapi import APIRouter, Depends, Response

from app.api.dependencies import require_current_user
from app.models.user import User
from app.schemas.user import UserResponse

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
