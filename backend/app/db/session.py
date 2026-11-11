from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.engine import create_database_url, create_engine, create_session_factory

settings = get_settings()
database_url = create_database_url(settings)
engine = create_engine(settings)
SessionFactory = create_session_factory(engine)


async def get_db() -> AsyncIterator[AsyncSession]:
    async with SessionFactory() as session:
        yield session
