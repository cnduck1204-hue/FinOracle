from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache.login_failures import LoginFailureStore
from app.core.config import get_settings
from app.core.exceptions import AppException
from app.models.user import User
from app.repositories.user import UserRepository
from app.schemas.auth import LoginRequest, RegisterRequest
from app.security.passwords import (
    DUMMY_PASSWORD_HASH,
    hash_password,
    verify_password,
)


async def register_user(
    payload: RegisterRequest,
    session: AsyncSession,
) -> User:
    # 哈希计算放在事务之前，避免计算期间占用数据库连接。
    password_hash = await hash_password(payload.password.get_secret_value())

    repository = UserRepository(session)

    try:
        async with session.begin():
            existing_user = await repository.get_by_username(payload.username)
            if existing_user is not None:
                raise AppException(
                    code="USER_USERNAME_EXISTS",
                    message="用户名已存在",
                    status_code=409,
                )

            existing_user = await repository.get_by_email(str(payload.email))
            if existing_user is not None:
                raise AppException(
                    code="USER_EMAIL_EXISTS",
                    message="邮箱已存在",
                    status_code=409,
                )

            user = User(
                username=payload.username,
                email=str(payload.email),
                password_hash=password_hash,
                role="user",
                status="active",
            )

            await repository.add(user)

    except IntegrityError as exc:
        error_args = getattr(exc.orig, "args", ())

        if error_args and error_args[0] == 1062:
            raise AppException(
                code="USER_ALREADY_EXISTS",
                message="用户名或邮箱已存在",
                status_code=409,
            ) from exc

        raise
    return user


async def authenticate_user(
    payload: LoginRequest,
    session: AsyncSession,
) -> User:
    repository = UserRepository(session)

    async with session.begin():
        user = await repository.get_by_identifier(payload.identifier)

    password_hash = user.password_hash if user is not None else DUMMY_PASSWORD_HASH

    password_matches = await verify_password(
        payload.password.get_secret_value(),
        password_hash,
    )

    if user is None or not password_matches:
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

    return user


async def authenticate_with_login_limit(
    payload: LoginRequest,
    session: AsyncSession,
    cache: Redis,
) -> User:
    settings = get_settings()

    failures = LoginFailureStore(
        cache,
        max_failures=settings.login_max_failures,
        lock_seconds=settings.login_lock_seconds,
    )

    try:
        retry_after = await failures.retry_after(payload.identifier)

        if retry_after > 0:
            raise AppException(
                code="AUTH_LOGIN_RATE_LIMITED",
                message="登录尝试过于频繁，请稍后重试",
                status_code=429,
            )

        try:
            user = await authenticate_user(payload, session)
        except AppException as exc:
            if exc.code == "AUTH_INVALID_CREDENTIALS":
                await failures.record_failure(payload.identifier)

            raise

        await failures.clear(payload.identifier)
        return user

    except RedisError as exc:
        raise AppException(
            code="AUTH_SERVICE_UNAVAILABLE",
            message="认证服务暂时不可用，请稍后重试",
            status_code=503,
        ) from exc
