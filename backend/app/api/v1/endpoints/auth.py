from typing import Annotated

from fastapi import APIRouter, Depends, Response, status
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_access_token
from app.cache.redis import get_redis
from app.db.session import get_db
from app.schemas.auth import (
    LoginRequest,
    LoginResponse,
    RefreshRequest,
    RegisterRequest,
)
from app.schemas.user import UserResponse
from app.services.auth import register_user
from app.services.login import login_user
from app.services.logout import logout_user
from app.services.refresh import refresh_tokens

router = APIRouter()


@router.post(
    "/register",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
)
async def register(
    payload: RegisterRequest,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> UserResponse:
    user = await register_user(payload, session)
    return UserResponse.model_validate(user)


@router.post(
    "/login",
    response_model=LoginResponse,
    status_code=status.HTTP_200_OK,
)
async def login(
    payload: LoginRequest,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_db)],
    cache: Annotated[Redis, Depends(get_redis)],
) -> LoginResponse:
    result = await login_user(
        payload,
        session,
        cache,
    )

    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"

    return result


@router.post("/refresh", response_model=LoginResponse)
async def refresh(
    payload: RefreshRequest,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_db)],
    cache: Annotated[Redis, Depends(get_redis)],
) -> LoginResponse:
    # Access tokens may already be expired; the service validates the refresh token.
    result = await refresh_tokens(
        payload.refresh_token.get_secret_value(), session, cache
    )
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return result


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    token: Annotated[str, Depends(require_access_token)],
    session: Annotated[AsyncSession, Depends(get_db)],
    cache: Annotated[Redis, Depends(get_redis)],
) -> Response:
    await logout_user(token, session, cache, all_sessions=False)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/logout-all", status_code=status.HTTP_204_NO_CONTENT)
async def logout_all(
    token: Annotated[str, Depends(require_access_token)],
    session: Annotated[AsyncSession, Depends(get_db)],
    cache: Annotated[Redis, Depends(get_redis)],
) -> Response:
    await logout_user(token, session, cache, all_sessions=True)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
