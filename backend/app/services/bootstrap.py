from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.config import Settings
from app.models.user import User
from app.repositories.user import UserRepository
from app.security.passwords import hash_password


async def bootstrap_admin(settings: Settings, session: AsyncSession) -> None:
    """Called under the initialization lock; never overwrite credentials or roles."""
    async with session.begin():
        existing = await session.scalar(select(User).where(User.role == "admin"))
        if existing is not None:
            return
        repository = UserRepository(session)
        if (
            await repository.get_by_username(settings.admin_username) is not None
            or await repository.get_by_email(settings.admin_email) is not None
        ):
            raise ValueError("初始化管理员账号与已有账号冲突")
        password_hash = await hash_password(settings.admin_password.get_secret_value())
        await repository.add(
            User(
                username=settings.admin_username,
                email=settings.admin_email,
                password_hash=password_hash,
                role="admin",
                status="active",
                auth_version=0,
            )
        )


async def initialize_admin(settings: Settings, engine: AsyncEngine) -> None:
    if settings.admin_username is None:
        return
    # Hold one connection until RELEASE_LOCK: committing must not return it to the pool.
    async with engine.connect() as connection:
        acquired = await connection.scalar(
            text("SELECT GET_LOCK('finoracle:bootstrap-admin', 10)")
        )
        await connection.commit()
        if acquired != 1:
            raise RuntimeError("无法取得管理员初始化锁")
        try:
            async with AsyncSession(bind=connection, expire_on_commit=False) as session:
                await bootstrap_admin(settings, session)
        finally:
            await connection.execute(
                text("SELECT RELEASE_LOCK('finoracle:bootstrap-admin')")
            )
            await connection.commit()
