"""Store chat session pinning and index pinned-first pagination."""

import sqlalchemy as sa
from alembic import op

revision = "c791a54293ed"
down_revision = "f84e0137ca8a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "chat_sessions",
        sa.Column("pinned", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index(
        "ix_chat_sessions_pinned_activity",
        "chat_sessions",
        ["user_id", "archived_at", "pinned", "updated_at", "id"],
    )
    op.drop_index("ix_chat_sessions_activity", table_name="chat_sessions")


def downgrade() -> None:
    op.create_index(
        "ix_chat_sessions_activity",
        "chat_sessions",
        ["user_id", "archived_at", "updated_at", "id"],
    )
    op.drop_index("ix_chat_sessions_pinned_activity", table_name="chat_sessions")
    op.drop_column("chat_sessions", "pinned")
