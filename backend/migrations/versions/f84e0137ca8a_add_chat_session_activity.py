"""Add activity and management fields to persisted chat sessions."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "f84e0137ca8a"
down_revision = "eca85b2701d3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("chat_sessions", sa.Column("title", sa.String(80)))
    op.add_column("chat_sessions", sa.Column("archived_at", mysql.DATETIME(fsp=6)))
    op.add_column("chat_sessions", sa.Column("updated_at", mysql.DATETIME(fsp=6)))
    op.execute("UPDATE chat_sessions SET updated_at = created_at")
    op.execute(
        """UPDATE chat_sessions AS s SET s.title = (
            SELECT LEFT(JSON_UNQUOTE(JSON_EXTRACT(e.payload, '$.question')), 80)
            FROM chat_session_events AS e
            WHERE e.session_id = s.id AND e.kind = 'question'
            ORDER BY e.seq LIMIT 1
        )"""
    )
    op.alter_column(
        "chat_sessions",
        "updated_at",
        existing_type=mysql.DATETIME(fsp=6),
        nullable=False,
    )
    op.create_index(
        "ix_chat_sessions_activity",
        "chat_sessions",
        ["user_id", "archived_at", "updated_at", "id"],
    )
    op.create_index(
        "ix_chat_session_events_turn",
        "chat_session_events",
        ["session_id", "turn_id", "seq"],
    )


def downgrade() -> None:
    op.drop_index("ix_chat_session_events_turn", table_name="chat_session_events")
    op.drop_index("ix_chat_sessions_activity", table_name="chat_sessions")
    op.drop_column("chat_sessions", "updated_at")
    op.drop_column("chat_sessions", "archived_at")
    op.drop_column("chat_sessions", "title")
