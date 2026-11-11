"""Store chunks and index state per ingest pipeline.

Chunks keep the existing rows under the default "structured" ingest. Additional
pipelines get their own chunks and a status row in version_indexes, so building one
never changes the version lifecycle.

Revision ID: a7d4c2f19b30
Revises: e43a1cf1d3d5
Create Date: 2026-11-10 18:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "a7d4c2f19b30"
down_revision: str | Sequence[str] | None = "e43a1cf1d3d5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_KINDS = sa.Enum("parse", "index", "cleanup", name="import_job_kind")
_NEW_KINDS = sa.Enum("parse", "index", "cleanup", "pipeline", name="import_job_kind")


def upgrade() -> None:
    op.add_column(
        "document_chunks",
        sa.Column(
            "ingest", sa.String(length=32), server_default="structured", nullable=False
        ),
    )
    op.add_column("document_chunks", sa.Column("bbox", sa.JSON(), nullable=True))
    op.add_column(
        "document_chunks", sa.Column("modality", sa.String(length=16), nullable=True)
    )
    op.drop_constraint("uq_document_chunks_ordinal", "document_chunks", type_="unique")
    op.create_unique_constraint(
        "uq_document_chunks_ordinal",
        "document_chunks",
        ["version_id", "ingest", "ordinal"],
    )
    op.alter_column(
        "import_jobs",
        "kind",
        existing_type=_OLD_KINDS,
        type_=_NEW_KINDS,
        existing_nullable=False,
    )
    op.add_column(
        "import_jobs", sa.Column("ingest", sa.String(length=32), nullable=True)
    )
    op.create_table(
        "version_indexes",
        sa.Column("version_id", sa.Uuid(), nullable=False),
        sa.Column("ingest", sa.String(length=32), nullable=False),
        sa.Column(
            "status",
            sa.Enum("building", "ready", "failed", name="version_index_status"),
            server_default="building",
            nullable=False,
        ),
        sa.Column("unit_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("parser_version", sa.String(length=32), nullable=True),
        sa.Column("stats", sa.JSON(), nullable=True),
        sa.Column("index_model", sa.String(length=255), nullable=True),
        sa.Column("indexed_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("updated_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.ForeignKeyConstraint(
            ["version_id"],
            ["document_versions.id"],
            name="fk_version_indexes_version_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("version_id", "ingest"),
    )


def downgrade() -> None:
    op.execute("DELETE FROM document_chunks WHERE ingest <> 'structured'")
    op.execute("DELETE FROM import_jobs WHERE kind = 'pipeline'")
    op.drop_table("version_indexes")
    op.drop_column("import_jobs", "ingest")
    op.alter_column(
        "import_jobs",
        "kind",
        existing_type=_NEW_KINDS,
        type_=_OLD_KINDS,
        existing_nullable=False,
    )
    op.drop_constraint("uq_document_chunks_ordinal", "document_chunks", type_="unique")
    op.create_unique_constraint(
        "uq_document_chunks_ordinal", "document_chunks", ["version_id", "ordinal"]
    )
    op.drop_column("document_chunks", "modality")
    op.drop_column("document_chunks", "bbox")
    op.drop_column("document_chunks", "ingest")
