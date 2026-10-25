from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache.sessions import SessionStore
from app.core.exceptions import AppException
from app.security.tokens import decode_token
from app.services.current_user import get_current_user


async def logout_user(
    token: str,
    session: AsyncSession,
    cache: Redis,
    *,
    all_sessions: bool = False,
) -> None:
    """Authenticate before revoking; never report success after a Redis failure."""
    user = await get_current_user(token, session, cache)
    claims = decode_token(token, expected_type="access")
    store = SessionStore(cache)
    try:
        if all_sessions:
            await store.revoke_all(user.id)
        else:
            await store.revoke(claims.sid)
    except RedisError as exc:
        raise AppException(
            code="AUTH_SERVICE_UNAVAILABLE",
            message="认证服务暂时不可用，请稍后重试",
            status_code=503,
        ) from exc
