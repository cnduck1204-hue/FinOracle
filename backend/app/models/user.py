from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import Enum, String, UniqueConstraint, Uuid
from sqlalchemy.dialects.mysql import DATETIME
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def utc_now() -> datetime:
    """MySQL DATETIME 按 UTC 存储；响应模型负责补回 UTC 时区标识。"""
    return datetime.now(UTC).replace(tzinfo=None)


class User(Base):
    __tablename__ = "users"

    __table_args__ = (
        UniqueConstraint("username", name="uq_users_username"),
        UniqueConstraint("email", name="uq_users_email"),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )

    username: Mapped[str] = mapped_column(String(32))
    email: Mapped[str] = mapped_column(String(254))
    password_hash: Mapped[str] = mapped_column(String(255))

    role: Mapped[str] = mapped_column(
        Enum(
            "user",
            "admin",
            name="user_role",
            validate_strings=True,
        ),
        default="user",
        server_default="user",
    )

    status: Mapped[str] = mapped_column(
        Enum(
            "active",
            "disabled",
            name="user_status",
            validate_strings=True,
        ),
        default="active",
        server_default="active",
    )

    last_login_at: Mapped[datetime | None] = mapped_column(
        DATETIME(fsp=6),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6),
        default=utc_now,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6),
        default=utc_now,
        onupdate=utc_now,
    )
