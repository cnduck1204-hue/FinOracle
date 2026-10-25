from collections.abc import AsyncIterator

from sqlalchemy import URL
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings

settings = get_settings()

database_url = URL.create(
    drivername="mysql+asyncmy",
    username=settings.mysql_user,
    password=settings.mysql_password.get_secret_value(),
    host=settings.mysql_host,
    port=settings.mysql_port,
    database=settings.mysql_database,
    query={"charset": "utf8mb4"},
)

engine = create_async_engine(
    database_url,
    echo=False,
    hide_parameters=True,
    pool_pre_ping=True,
    pool_recycle=1800,
    pool_timeout=5,
    connect_args={"connect_timeout": 5},
)

SessionFactory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db() -> AsyncIterator[AsyncSession]:
    async with SessionFactory() as session:
        yield session
