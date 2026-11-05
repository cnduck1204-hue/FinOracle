"""数据库连接工厂。导入本模块不读取任何配置，worker 可只用 DatabaseSettings 创建连接。"""

from sqlalchemy import URL
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import DatabaseSettings


def create_database_url(settings: DatabaseSettings) -> URL:
    return URL.create(
        drivername="mysql+asyncmy",
        username=settings.mysql_user,
        password=settings.mysql_password.get_secret_value(),
        host=settings.mysql_host,
        port=settings.mysql_port,
        database=settings.mysql_database,
        query={"charset": "utf8mb4"},
    )


# worker 使用的隔离级别。MySQL 默认的 REPEATABLE READ 下，认领任务的锁定读会加间隙锁，
# 阻塞其他 worker 更新各自任务的索引项，并发认领因此串行甚至超时；READ COMMITTED 不加间隙锁
WORKER_ISOLATION_LEVEL = "READ COMMITTED"


def create_engine(
    settings: DatabaseSettings, *, isolation_level: str | None = None
) -> AsyncEngine:
    """API 与 worker 共用的连接池参数；isolation_level 为空时使用数据库默认值。"""
    options = {"isolation_level": isolation_level} if isolation_level else {}
    return create_async_engine(
        create_database_url(settings),
        echo=False,
        hide_parameters=True,
        pool_pre_ping=True,
        pool_recycle=1800,
        pool_timeout=5,
        connect_args={"connect_timeout": 5},
        **options,
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
