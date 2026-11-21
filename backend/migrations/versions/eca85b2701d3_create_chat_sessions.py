"""Store owner-scoped chat sessions and their ordered turn events."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "eca85b2701d3"
down_revision = "a7d4c2f19b30"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "chat_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("next_seq", sa.Integer(), server_default="0", nullable=False),
        sa.Column("active_turn_id", sa.Uuid(), nullable=True),
        sa.Column("active_until", mysql.DATETIME(fsp=6), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_chat_sessions_user_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_chat_sessions_user_id", "chat_sessions", ["user_id"])
    op.create_table(
        "chat_session_events",
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("turn_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["chat_sessions.id"],
            name="fk_chat_session_events_session_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("session_id", "seq"),
    )
    op.create_index(
        "ix_chat_session_events_kind",
        "chat_session_events",
        ["session_id", "kind", "seq"],
    )


def downgrade() -> None:
    op.drop_table("chat_session_events")
    op.drop_table("chat_sessions")
