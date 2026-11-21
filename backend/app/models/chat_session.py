from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import JSON, ForeignKey, Index, Integer, String, Uuid, false
from sqlalchemy.dialects.mysql import DATETIME
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, utc_now


class ChatSession(Base):
    __tablename__ = "chat_sessions"
    __table_args__ = (
        Index("ix_chat_sessions_user_id", "user_id"),
        Index(
            "ix_chat_sessions_pinned_activity",
            "user_id",
            "archived_at",
            "pinned",
            "updated_at",
            "id",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid4
    )
    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", name="fk_chat_sessions_user_id", ondelete="CASCADE"),
    )
    created_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6), default=utc_now)
    title: Mapped[str | None] = mapped_column(String(80))
    archived_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6))
    pinned: Mapped[bool] = mapped_column(default=False, server_default=false())
    next_seq: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    active_turn_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    active_until: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6))


class ChatSessionEvent(Base):
    __tablename__ = "chat_session_events"
    __table_args__ = (
        Index("ix_chat_session_events_kind", "session_id", "kind", "seq"),
        Index("ix_chat_session_events_turn", "session_id", "turn_id", "seq"),
    )

    session_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "chat_sessions.id",
            name="fk_chat_session_events_session_id",
            ondelete="CASCADE",
        ),
        primary_key=True,
    )
    seq: Mapped[int] = mapped_column(Integer, primary_key=True)
    turn_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True))
    kind: Mapped[str] = mapped_column(String(16))
    payload: Mapped[dict] = mapped_column(JSON)
