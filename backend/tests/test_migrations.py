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
