import json
import re
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def migration_probe():
    # Use a fresh process so imports from other tests cannot register the model.
    script = """
import io
import json

from alembic import command
from alembic.config import Config

output = io.StringIO()
config = Config("alembic.ini", output_buffer=output)
command.upgrade(config, "head", sql=True)

from app.db.base import Base

print(json.dumps({"tables": sorted(Base.metadata.tables), "sql": output.getvalue()}))
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=True,
        timeout=15,
    )
    return json.loads(result.stdout)


def test_migration_environment_registers_users(migration_probe):
    assert "users" in migration_probe["tables"]


def test_initial_migration_creates_users_with_unique_identifiers(migration_probe):
    sql = migration_probe["sql"]
    assert re.search(r"CREATE TABLE\s+`?users`?\s*\(", sql)
    assert "CONSTRAINT uq_users_username UNIQUE (username)" in sql
    assert "CONSTRAINT uq_users_email UNIQUE (email)" in sql


def test_migration_environment_registers_ingestion_tables(migration_probe):
    assert {"documents", "document_versions", "document_chunks", "import_jobs"} <= set(
        migration_probe["tables"]
    )


def test_ingestion_migration_enforces_version_invariants(migration_probe):
    sql = migration_probe["sql"]
    for table in ("documents", "document_versions", "document_chunks", "import_jobs"):
        assert re.search(rf"CREATE TABLE\s+`?{table}`?\s*\(", sql)
    # 每个制度最多一个已发布版本：生成列加唯一约束
    assert (
        "GENERATED ALWAYS AS (CASE WHEN status = 'published' THEN document_id END) "
        "STORED" in sql
    )
    assert (
        "CONSTRAINT uq_document_versions_published_document "
        "UNIQUE (published_document_id)" in sql
    )
    assert "UNIQUE (document_id, sha256)" in sql
    # 切片的 document_id 必须与所属版本一致，删除版本时级联删除切片和任务
    assert re.search(
        r"CONSTRAINT fk_document_chunks_version FOREIGN KEY\(version_id, document_id\) "
        r"REFERENCES document_versions \(id, document_id\) ON DELETE CASCADE",
        sql,
    )
    assert re.search(
        r"CONSTRAINT fk_import_jobs_version_id FOREIGN KEY\(version_id\) "
        r"REFERENCES document_versions \(id\) ON DELETE CASCADE",
        sql,
    )
    assert "content MEDIUMTEXT NOT NULL" in sql
