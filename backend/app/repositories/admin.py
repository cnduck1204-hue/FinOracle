from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User


class AdminRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_users(
        self,
        *,
        page: int,
        page_size: int,
        q: str | None,
        role: str | None,
        status: str | None,
    ) -> tuple[list[User], int]:
        filters = []
        if q:
            pattern = (
                "%"
                + q.lower()
                .replace("\\", "\\\\")
                .replace("%", "\\%")
                .replace("_", "\\_")
                + "%"
            )
            filters.append(
                or_(
                    User.username.like(pattern, escape="\\"),
                    User.email.like(pattern, escape="\\"),
                )
            )
        if role:
            filters.append(User.role == role)
        if status:
            filters.append(User.status == status)
        total = await self.session.scalar(
            select(func.count()).select_from(User).where(*filters)
        )
        users = await self.session.scalars(
            select(User)
            .where(*filters)
            .order_by(User.created_at.desc(), User.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return list(users), int(total or 0)

    async def get_user(self, user_id: UUID, *, for_update: bool = False) -> User | None:
        return await self.session.get(
            User, user_id, populate_existing=True, with_for_update=for_update
        )
