from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_username(self, username: str) -> User | None:
        statement = select(User).where(User.username == username)
        return await self.session.scalar(statement)

    async def get_by_email(self, email: str) -> User | None:
        statement = select(User).where(User.email == email)
        return await self.session.scalar(statement)

    async def add(self, user: User) -> None:
        self.session.add(user)
        await self.session.flush()

    async def get_by_identifier(
        self,
        identifier: str,
    ) -> User | None:
        if "@" in identifier:
            user = await self.get_by_email(identifier)
            stored_identifier = user.email if user is not None else None
        else:
            user = await self.get_by_username(identifier)
            stored_identifier = user.username if user is not None else None

        # MySQL 的 unicode_ci 会合并重音/不可见字符。登录只接受规范化后的
        # 精确标识，确保同一账号不能通过这些别名绕过 Redis 失败次数限制。
        return user if stored_identifier == identifier else None

    async def get_by_id_for_update(
        self,
        user_id: UUID,
    ) -> User | None:
        return await self.session.get(
            User,
            user_id,
            populate_existing=True,
            with_for_update=True,
        )

    async def get_by_id(
        self,
        user_id: UUID,
    ) -> User | None:
        return await self.session.get(
            User,
            user_id,
            populate_existing=True,
        )
