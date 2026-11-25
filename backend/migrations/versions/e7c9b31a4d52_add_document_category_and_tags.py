"""Add category and tags columns to documents for taxonomy and library filtering."""

import sqlalchemy as sa
from alembic import op

revision = "e7c9b31a4d52"
down_revision = "d4f8a21e7b09"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "documents", sa.Column("category", sa.String(length=64), nullable=True)
    )
    op.add_column("documents", sa.Column("tags", sa.JSON(), nullable=True))
    op.create_index("ix_documents_category", "documents", ["category"])


def downgrade() -> None:
    op.drop_index("ix_documents_category", table_name="documents")
    op.drop_column("documents", "tags")
    op.drop_column("documents", "category")
