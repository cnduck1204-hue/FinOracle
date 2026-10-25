import logging
from uuid import uuid4

from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache.sessions import SessionStore
from app.core.exceptions import AppException
from app.models.user import utc_now
from app.repositories.user import UserRepository
from app.schemas.auth import LoginRequest, LoginResponse
from app.schemas.session import SessionRecord
from app.schemas.user import UserResponse
from app.security.tokens import create_token, decode_token
from app.services.auth import authenticate_with_login_limit

logger = logging.getLogger(__name__)


async def login_user(
    payload: LoginRequest,
    session: AsyncSession,
    cache: Redis,
) -> LoginResponse:
    """密码验证后重新锁定用户，只有 MySQL 提交成功才返回双令牌。"""
    session_id = uuid4()
    store = SessionStore(cache)
    session_write_started = False

    try:
        authenticated = await authenticate_with_login_limit(
            payload,
            session,
            cache,
        )

        async with session.begin():
            repository = UserRepository(session)
            user = await repository.get_by_id_for_update(authenticated.id)

            if user is None:
                raise AppException(
                    code="AUTH_INVALID_CREDENTIALS",
                    message="账号或密码错误",
                    status_code=401,
                )

            if user.status != "active":
                raise AppException(
                    code="USER_DISABLED",
                    message="账号已被禁用",
                    status_code=403,
                )

            access_token = create_token(
                user_id=user.id,
                role=user.role,
                session_id=session_id,
                token_id=uuid4(),
                token_type="access",
            )

            refresh_token = create_token(
                user_id=user.id,
                role=user.role,
                session_id=session_id,
                token_id=uuid4(),
                token_type="refresh",
            )

            access_claims = decode_token(
                access_token,
                expected_type="access",
            )

            refresh_claims = decode_token(
                refresh_token,
                expected_type="refresh",
            )

            record = SessionRecord(
                id=session_id,
                user_id=user.id,
                refresh_jti=refresh_claims.jti,
                expires_at=refresh_claims.exp,
                created_at=refresh_claims.iat,
            )

            user.last_login_at = utc_now()
            await session.flush()

            response = LoginResponse(
                access_token=access_token,
                refresh_token=refresh_token,
                expires_in=access_claims.exp - access_claims.iat,
                user=UserResponse.model_validate(user),
            )

            # 写入前标记：Redis 超时也可能已经落盘，异常时必须尝试撤销。
            session_write_started = True
            await store.create(record)

        return response
    except (RedisError, SQLAlchemyError) as exc:
        if session_write_started:
            try:
                await store.revoke(session_id)
            except RedisError:
                logger.warning(
                    "login_session_revoke_failed",
                    extra={"session_id": str(session_id)},
                    exc_info=True,
                )

        raise AppException(
            code="AUTH_SERVICE_UNAVAILABLE",
            message="认证服务暂时不可用，请稍后重试",
            status_code=503,
        ) from exc
