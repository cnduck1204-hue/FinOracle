from datetime import UTC, datetime

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


def utc_now() -> datetime:
    """MySQL DATETIME 按 UTC 存储；响应模型负责补回 UTC 时区标识。"""
    return datetime.now(UTC).replace(tzinfo=None)
