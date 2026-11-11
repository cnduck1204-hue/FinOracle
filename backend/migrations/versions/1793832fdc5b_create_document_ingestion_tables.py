"""Create document, version, chunk and import job tables for ingestion.

Revision ID: 1793832fdc5b
Revises: 7c84ef019b21
Create Date: 2026-11-05 15:11:13.082885

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "1793832fdc5b"
down_revision: str | Sequence[str] | None = "7c84ef019b21"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "documents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("external_id", sa.String(length=128), nullable=True),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("issuer", sa.String(length=255), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("updated_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name="fk_documents_created_by"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("external_id", name="uq_documents_external_id"),
    )
    op.create_table(
        "document_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "uploaded",
                "parsing",
                "parsed",
                "indexing",
                "ready",
                "published",
                "archived",
                "failed",
                name="document_version_status",
            ),
            server_default="uploaded",
            nullable=False,
        ),
        # 仅已发布版本有值；与下方唯一约束一起保证每个制度最多一个已发布版本
        sa.Column(
            "published_document_id",
            sa.Uuid(),
            sa.Computed(
                "CASE WHEN status = 'published' THEN document_id END", persisted=True
            ),
            nullable=True,
        ),
        sa.Column("document_number", sa.String(length=255), nullable=True),
        sa.Column("version_note", sa.String(length=255), nullable=True),
        sa.Column("published_date", sa.Date(), nullable=True),
        sa.Column("effective_date", sa.Date(), nullable=True),
        sa.Column(
            "validity_status",
            sa.Enum(
                "unknown",
                "effective",
                "not_yet_effective",
                "amended",
                "repealed",
                name="document_validity_status",
            ),
            server_default="unknown",
            nullable=False,
        ),
        sa.Column("validity_checked_at", sa.Date(), nullable=True),
        sa.Column("source_url", sa.String(length=2048), nullable=True),
        sa.Column("download_url", sa.String(length=2048), nullable=True),
        sa.Column("file_name", sa.String(length=255), nullable=False),
        sa.Column("file_type", sa.String(length=16), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("object_key", sa.String(length=512), nullable=False),
        sa.Column("pdf_object_key", sa.String(length=512), nullable=True),
        sa.Column("parse_options", sa.JSON(), nullable=False),
        sa.Column("parser_version", sa.String(length=32), nullable=True),
        sa.Column(
            "parse_status",
            sa.Enum("completed", "needs_ocr", "empty", name="document_parse_status"),
            nullable=True,
        ),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("chunk_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("expected_articles", sa.Integer(), nullable=True),
        sa.Column("parse_stats", sa.JSON(), nullable=True),
        sa.Column("parse_warnings", sa.JSON(), nullable=True),
        sa.Column("index_model", sa.String(length=128), nullable=True),
        sa.Column("indexed_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("published_by", sa.Uuid(), nullable=True),
        sa.Column("published_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("updated_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name="fk_document_versions_created_by"
        ),
        sa.ForeignKeyConstraint(
            ["document_id"], ["documents.id"], name="fk_document_versions_document_id"
        ),
        sa.ForeignKeyConstraint(
            ["published_by"], ["users.id"], name="fk_document_versions_published_by"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "document_id", "sha256", name="uq_document_versions_document_sha256"
        ),
        sa.UniqueConstraint(
            "id", "document_id", name="uq_document_versions_id_document"
        ),
        sa.UniqueConstraint(
            "published_document_id", name="uq_document_versions_published_document"
        ),
    )
    op.create_index("ix_document_versions_status", "document_versions", ["status"])
    op.create_table(
        "document_chunks",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("version_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("external_id", sa.String(length=128), nullable=True),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("chapter", sa.String(length=255), server_default="", nullable=False),
        sa.Column("section", sa.String(length=255), server_default="", nullable=False),
        sa.Column("article", sa.String(length=255), server_default="", nullable=False),
        sa.Column("heading", sa.String(length=512), server_default="", nullable=False),
        sa.Column("part", sa.SmallInteger(), server_default="1", nullable=False),
        sa.Column("part_count", sa.SmallInteger(), server_default="1", nullable=False),
        sa.Column("content", mysql.MEDIUMTEXT(), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("page_start", sa.Integer(), nullable=True),
        sa.Column("page_end", sa.Integer(), nullable=True),
        sa.Column("sources", sa.JSON(), nullable=False),
        # 复合外键保证切片的 document_id 与所属版本一致；删除版本时一并删除切片
        sa.ForeignKeyConstraint(
            ["version_id", "document_id"],
            ["document_versions.id", "document_versions.document_id"],
            name="fk_document_chunks_version",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("version_id", "ordinal", name="uq_document_chunks_ordinal"),
    )
    op.create_index(
        "ix_document_chunks_version_document",
        "document_chunks",
        ["version_id", "document_id"],
    )
    op.create_table(
        "import_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("version_id", sa.Uuid(), nullable=False),
        sa.Column(
            "kind",
            sa.Enum("parse", "index", "cleanup", name="import_job_kind"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "queued",
                "running",
                "succeeded",
                "failed",
                "cancelled",
                name="import_job_status",
            ),
            server_default="queued",
            nullable=False,
        ),
        sa.Column("stage", sa.String(length=32), nullable=True),
        sa.Column("progress_current", sa.Integer(), server_default="0", nullable=False),
        sa.Column("progress_total", sa.Integer(), nullable=True),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default="3", nullable=False),
        sa.Column("run_after", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("locked_by", sa.String(length=64), nullable=True),
        sa.Column("lease_expires_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.String(length=1000), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("started_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("finished_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("updated_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name="fk_import_jobs_created_by"
        ),
        sa.ForeignKeyConstraint(
            ["version_id"],
            ["document_versions.id"],
            name="fk_import_jobs_version_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_import_jobs_status_run_after", "import_jobs", ["status", "run_after"]
    )
    op.create_index("ix_import_jobs_version_id", "import_jobs", ["version_id"])


def downgrade() -> None:
    # 外键依赖的索引随表一起删除；单独先删索引会被 MySQL 拒绝
    op.drop_table("import_jobs")
    op.drop_table("document_chunks")
    op.drop_table("document_versions")
    op.drop_table("documents")
