"""Index running import jobs by lease expiry for index-ordered claiming.

Workers claim with FOR UPDATE SKIP LOCKED. Without an index matching the sort, MySQL
sorts every candidate first and locks them all, so concurrent workers find nothing.

Revision ID: e43a1cf1d3d5
Revises: 1793832fdc5b
Create Date: 2026-11-05 15:39:52.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "e43a1cf1d3d5"
down_revision: str | Sequence[str] | None = "1793832fdc5b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_import_jobs_status_lease", "import_jobs", ["status", "lease_expires_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_import_jobs_status_lease", table_name="import_jobs")
