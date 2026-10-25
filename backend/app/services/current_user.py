from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache.sessions import SessionStore
from app.core.exceptions import AppException
from app.models.user import User
from app.repositories.user import UserRepository
from app.security.tokens import decode_token


async def get_current_user(
    token: str,
    session: AsyncSession,
    cache: Redis,
) -> User:
    claims = decode_token(
        token,
        expected_type="access",
    )

    try:
        record = await SessionStore(cache).get(claims.sid)
    except (RedisError, ValueError) as exc:
        raise AppException(
            code="AUTH_SERVICE_UNAVAILABLE",
            message="认证服务暂时不可用，请稍后重试",
            status_code=503,
        ) from exc

    if record is None or record.revoked or record.user_id != claims.sub:
        raise AppException(
            code="AUTH_INVALID_TOKEN",
            message="令牌无效或已过期",
            status_code=401,
        )

    try:
        async with session.begin():
            repository = UserRepository(session)
            user = await repository.get_by_id(claims.sub)
    except SQLAlchemyError as exc:
        raise AppException(
            code="AUTH_SERVICE_UNAVAILABLE",
            message="认证服务暂时不可用，请稍后重试",
            status_code=503,
        ) from exc

    if user is None:
        raise AppException(
            code="AUTH_INVALID_TOKEN",
            message="令牌无效或已过期",
            status_code=401,
        )

    if user.status != "active":
        raise AppException(
            code="USER_DISABLED",
            message="账号已被禁用",
            status_code=403,
        )

    return user
