from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache.redis import get_redis
from app.core.exceptions import AppException
from app.db.session import get_db
from app.models.user import User
from app.services.current_user import get_current_user

bearer_scheme = HTTPBearer(
    auto_error=False,
    scheme_name="AccessToken",
)


async def require_access_token(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(bearer_scheme),
    ],
) -> str:
    if credentials is None:
        raise AppException(
            code="AUTH_REQUIRED",
            message="请先登录",
            status_code=401,
        )
    return credentials.credentials


async def require_current_user(
    token: Annotated[str, Depends(require_access_token)],
    session: Annotated[AsyncSession, Depends(get_db)],
    cache: Annotated[Redis, Depends(get_redis)],
) -> User:
    return await get_current_user(token, session, cache)
