from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException
from app.models.user import User
from app.repositories.user import UserRepository
from app.schemas.profile import ProfileUpdateRequest


async def update_profile(
    payload: ProfileUpdateRequest,
    user_id: UUID,
    session: AsyncSession,
) -> User:
    repository = UserRepository(session)

    try:
        async with session.begin():
            user = await repository.get_by_id_for_update(user_id)
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

            if "username" in payload.model_fields_set:
                existing = await repository.get_by_username(payload.username)
                if existing is not None and existing.id != user.id:
                    raise AppException(
                        code="USER_USERNAME_EXISTS",
                        message="用户名已存在",
                        status_code=409,
                    )

            if "email" in payload.model_fields_set:
                existing = await repository.get_by_email(str(payload.email))
                if existing is not None and existing.id != user.id:
                    raise AppException(
                        code="USER_EMAIL_EXISTS",
                        message="邮箱已存在",
                        status_code=409,
                    )

            if "username" in payload.model_fields_set:
                user.username = payload.username
            if "email" in payload.model_fields_set:
                user.email = str(payload.email)

            await session.flush()
            await session.refresh(user)

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
