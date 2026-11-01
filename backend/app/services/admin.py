from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException
from app.models.user import User
from app.repositories.admin import AdminRepository
from app.schemas.admin import UserPage
from app.schemas.user import UserResponse


def _not_found() -> AppException:
    return AppException(code="USER_NOT_FOUND", message="用户不存在", status_code=404)


async def list_users(
    session: AsyncSession,
    *,
    page: int,
    page_size: int,
    q: str | None,
    role: str | None,
    status: str | None,
) -> UserPage:
    async with session.begin():
        users, total = await AdminRepository(session).list_users(
            page=page, page_size=page_size, q=q, role=role, status=status
        )
        return UserPage(
            items=[UserResponse.model_validate(user) for user in users],
            total=total,
            page=page,
            page_size=page_size,
        )


async def get_user(user_id: UUID, session: AsyncSession) -> User:
    async with session.begin():
        user = await AdminRepository(session).get_user(user_id)
        if user is None:
            raise _not_found()
        return user


async def set_user_status(user_id: UUID, status: str, session: AsyncSession) -> User:
    async with session.begin():
        user = await AdminRepository(session).get_user(user_id, for_update=True)
        if user is None:
            raise _not_found()
        if user.role == "admin":
            raise AppException(
                code="ADMIN_PROTECTED",
                message="不能通过此接口修改管理员状态",
                status_code=403,
            )
        if user.status != status:
            user.status = status
            if status == "disabled":
                user.auth_version = (user.auth_version or 0) + 1
            await session.flush()
        return user
