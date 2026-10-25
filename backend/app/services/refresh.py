import logging
from uuid import uuid4

from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache.sessions import SessionStore
from app.core.exceptions import AppException
from app.repositories.user import UserRepository
from app.schemas.auth import LoginResponse
from app.schemas.user import UserResponse
from app.security.tokens import create_token, decode_token

logger = logging.getLogger(__name__)


def _invalid_refresh_token() -> AppException:
    return AppException(
        code="AUTH_INVALID_TOKEN",
        message="令牌无效或已过期",
        status_code=401,
    )


async def refresh_tokens(
    refresh_token: str,
    session: AsyncSession,
    cache: Redis,
) -> LoginResponse:
    """沿用 sid 轮换双令牌；旧 jti 重放由 Redis 原子操作撤销整个会话。"""

    claims = decode_token(
        refresh_token,
        expected_type="refresh",
    )

    store = SessionStore(cache)
    rotation_started = False

    try:
        record = await store.get(claims.sid)

        if record is None or record.revoked or record.user_id != claims.sub:
            raise _invalid_refresh_token()

        async with session.begin():
            repository = UserRepository(session)
            user = await repository.get_by_id(claims.sub)

            if user is None:
                raise _invalid_refresh_token()

            if user.status != "active":
                raise AppException(
                    code="USER_DISABLED",
                    message="账号已被禁用",
                    status_code=403,
                )

            access_token = create_token(
                user_id=user.id,
                role=user.role,
                session_id=claims.sid,
                token_id=uuid4(),
                token_type="access",
            )

            new_refresh_token = create_token(
                user_id=user.id,
                role=user.role,
                session_id=claims.sid,
                token_id=uuid4(),
                token_type="refresh",
            )

            access_claims = decode_token(
                access_token,
                expected_type="access",
            )

            refresh_claims = decode_token(
                new_refresh_token,
                expected_type="refresh",
            )

            response = LoginResponse(
                access_token=access_token,
                refresh_token=new_refresh_token,
                expires_in=access_claims.exp - access_claims.iat,
                user=UserResponse.model_validate(user),
            )

        # 先完成数据库事务和响应校验，避免失败请求提前消耗旧刷新令牌。
        rotation_started = True

        result = await store.rotate(
            session_id=claims.sid,
            user_id=claims.sub,
            expected_refresh_jti=claims.jti,
            new_refresh_jti=refresh_claims.jti,
            expires_at=refresh_claims.exp,
        )

        if result != "rotated":
            raise _invalid_refresh_token()

        return response

    except (RedisError, SQLAlchemyError, ValueError) as exc:
        if rotation_started:
            try:
                await store.revoke(claims.sid)
            except RedisError:
                logger.warning(
                    "refresh_session_revoke_failed",
                    extra={"session_id": str(claims.sid)},
                    exc_info=True,
                )

        raise AppException(
            code="AUTH_SERVICE_UNAVAILABLE",
            message="认证服务暂时不可用，请稍后重试",
            status_code=503,
        ) from exc
