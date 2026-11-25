"""Create owner-scoped favorite folders and favorites tables."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "d4f8a21e7b09"
down_revision = "c791a54293ed"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "favorite_folders",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("parent_id", sa.Uuid(), nullable=True),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("position", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("updated_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_favorite_folders_user_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["parent_id"],
            ["favorite_folders.id"],
            name="fk_favorite_folders_parent_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_favorite_folders_user_parent",
        "favorite_folders",
        ["user_id", "parent_id"],
    )
    op.create_table(
        "favorites",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("folder_id", sa.Uuid(), nullable=True),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("subtitle", sa.String(length=512), server_default="", nullable=False),
        sa.Column("note", sa.String(length=500), nullable=True),
        sa.Column("source_key", sa.String(length=128), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("updated_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_favorites_user_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["folder_id"],
            ["favorite_folders.id"],
            name="fk_favorites_folder_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_favorites_user_folder",
        "favorites",
        ["user_id", "folder_id", "created_at"],
    )
    op.create_index(
        "ix_favorites_user_source",
        "favorites",
        ["user_id", "source_key"],
    )


def downgrade() -> None:
    op.drop_index("ix_favorites_user_source", table_name="favorites")
    op.drop_index("ix_favorites_user_folder", table_name="favorites")
    op.drop_table("favorites")
    op.drop_index("ix_favorite_folders_user_parent", table_name="favorite_folders")
    op.drop_table("favorite_folders")
