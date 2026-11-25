"""用户收藏夹与收藏项。

每个用户拥有独立的收藏夹树（FavoriteFolder，通过 parent_id 形成树形结构，
parent_id 为 NULL 表示根目录「收藏夹栏」下的文件夹）和收藏项（Favorite）。
收藏项支持三类来源：问答记录（qa）、制度条文（chunk）、自定义链接（link）。
"""

from datetime import datetime
from typing import Any, Literal, get_args
from uuid import UUID, uuid4

from sqlalchemy import JSON, ForeignKey, Index, Integer, String, Uuid
from sqlalchemy.dialects.mysql import DATETIME
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, utc_now

FavoriteKind = Literal["qa", "chunk", "link"]
FAVORITE_KINDS: tuple[FavoriteKind, ...] = get_args(FavoriteKind)


class FavoriteFolder(Base):
    """用户收藏夹文件夹，支持多级嵌套。"""

    __tablename__ = "favorite_folders"
    __table_args__ = (Index("ix_favorite_folders_user_parent", "user_id", "parent_id"),)

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid4
    )
    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", name="fk_favorite_folders_user_id", ondelete="CASCADE"),
    )
    parent_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "favorite_folders.id",
            name="fk_favorite_folders_parent_id",
            ondelete="CASCADE",
        ),
        default=None,
    )
    name: Mapped[str] = mapped_column(String(80))
    position: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6), default=utc_now, onupdate=utc_now
    )


class Favorite(Base):
    """用户收藏条目，按 folder_id 归类；folder_id 为 NULL 时位于收藏夹栏根目录。"""

    __tablename__ = "favorites"
    __table_args__ = (
        Index("ix_favorites_user_folder", "user_id", "folder_id", "created_at"),
        Index("ix_favorites_user_source", "user_id", "source_key"),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid4
    )
    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", name="fk_favorites_user_id", ondelete="CASCADE"),
    )
    folder_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "favorite_folders.id",
            name="fk_favorites_folder_id",
            ondelete="CASCADE",
        ),
        default=None,
    )
    kind: Mapped[str] = mapped_column(String(16))
    title: Mapped[str] = mapped_column(String(255))
    subtitle: Mapped[str] = mapped_column(String(512), default="", server_default="")
    note: Mapped[str | None] = mapped_column(String(500), default=None)
    source_key: Mapped[str | None] = mapped_column(String(128), default=None)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6), default=utc_now, onupdate=utc_now
    )
