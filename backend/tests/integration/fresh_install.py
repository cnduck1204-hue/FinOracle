"""Validate first boot using isolated, temporary Compose containers and volumes."""

import secrets
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import httpx
import yaml

from app.core.config import Settings
from scripts.init_env import initialize_env
from tests.integration import containers as container_smoke


def check_fresh_environment():
    backend = Path(__file__).resolve().parents[2]
    project = f"finoracle-smoke-{uuid4().hex[:12]}"
    config = yaml.safe_load((backend / "compose.yaml").read_text())
    config["name"] = project
    for service in ("mysql", "redis"):
        config["services"][service].pop("ports", None)
    api = config["services"]["api"]
    api.pop("build", None)
    api["ports"] = [
        {"target": 8000, "published": "0", "host_ip": "127.0.0.1", "protocol": "tcp"}
    ]

    with tempfile.TemporaryDirectory(prefix="finoracle-container-test-") as directory:
        path = Path(directory) / "compose.yaml"
        path.write_text(yaml.safe_dump(config))
        # Exercise the documented first-run path with fresh, unrelated credentials.
        sample = Path(directory) / ".env.example"
        sample.write_text((backend / ".env.example").read_text())
        initialize_env(Path(directory))
        env_file = Path(directory) / ".env"
        admin_password = "FreshAdmin9" + secrets.token_hex(16)
        env_text = env_file.read_text()
        for key, value in {
            "ADMIN_USERNAME": "fresh_admin",
            "ADMIN_EMAIL": "fresh_admin@example.com",
            "ADMIN_PASSWORD": admin_password,
        }.items():
            env_text = env_text.replace(f"{key}=\n", f"{key}={value}\n")
        env_file.write_text(env_text)
        command = [
            "docker",
            "compose",
            "--progress",
            "plain",
            "--env-file",
            str(env_file),
            "-p",
            project,
            "-f",
            str(path),
        ]

        def compose(*args):
            result = subprocess.run(
                [*command, *args],
                cwd=backend,
                capture_output=True,
                text=True,
                check=True,
                timeout=30,
            )
            return result.stdout.strip()

        try:
            subprocess.run(
                [*command, "up", "-d", "--wait", "--wait-timeout", "150", "--no-build"],
                cwd=backend,
                check=True,
                timeout=180,
            )
            with patch.object(container_smoke, "compose", compose):
                with patch.object(
                    container_smoke,
                    "get_settings",
                    lambda: Settings(_env_file=env_file),
                ):
                    container_smoke.check_containers()
            print("PASS: Fresh database is migrated automatically during API startup")
            bootstrap_check = """
import asyncio
from sqlalchemy import select, func
from app.core.config import get_settings
from app.db.session import engine, SessionFactory
from app.models.user import User
from app.services.bootstrap import initialize_admin
from app.security.passwords import verify_password

async def check():
    settings = get_settings()
    async with SessionFactory() as session:
        admin = await session.scalar(select(User).where(User.role == 'admin'))
        assert admin.username == settings.admin_username and admin.auth_version == 0
        original = admin.password_hash
        assert await verify_password(settings.admin_password.get_secret_value(), original)
    await asyncio.gather(initialize_admin(settings, engine), initialize_admin(settings, engine))
    changed = settings.model_copy(update={'admin_password': type(settings.admin_password)('ChangedOnlyForTest123')})
    await initialize_admin(changed, engine)
    async with SessionFactory() as session:
        assert await session.scalar(select(func.count()).select_from(User).where(User.role == 'admin')) == 1
        admin = await session.scalar(select(User).where(User.role == 'admin'))
        assert admin.password_hash == original
    await engine.dispose()
asyncio.run(check())
print('PASS: Initial administrator is created; concurrent/repeated initialization preserves its password')
"""
            print(compose("exec", "-T", "api", "python", "-c", bootstrap_check))
            address = compose("port", "api", "8000").splitlines()[0]
            prefix = Settings(_env_file=env_file).api_v1_prefix.rstrip("/")
            with httpx.Client(
                base_url=f"http://{address}{prefix}", timeout=15, trust_env=False
            ) as client:
                login = client.post(
                    "/auth/login",
                    json={
                        "identifier": "fresh_admin",
                        "password": admin_password,
                    },
                )
                assert login.status_code == 200
                original_id = login.json()["user"]["id"]
                renamed = client.patch(
                    "/users/me",
                    headers={
                        "Authorization": "Bearer " + login.json()["access_token"],
                    },
                    json={
                        "username": "fresh_admin_renamed",
                        "email": "renamed_admin@example.com",
                    },
                )
                assert renamed.status_code == 200
                assert renamed.json()["id"] == original_id
            compose("restart", "api")
            compose("up", "-d", "--wait", "--wait-timeout", "90", "--no-build")
            renamed_check = bootstrap_check.replace(
                "admin.username == settings.admin_username",
                "admin.username == 'fresh_admin_renamed'",
            )
            print(compose("exec", "-T", "api", "python", "-c", renamed_check))
            address = compose("port", "api", "8000").splitlines()[0]
            with httpx.Client(
                base_url=f"http://{address}{prefix}", timeout=15, trust_env=False
            ) as client:
                login = client.post(
                    "/auth/login",
                    json={
                        "identifier": "renamed_admin@example.com",
                        "password": admin_password,
                    },
                )
                assert login.status_code == 200
                assert login.json()["user"]["id"] == original_id
                assert login.json()["user"]["username"] == "fresh_admin_renamed"
            print(
                "PASS: Administrator profile changes survive restart with the original initialization config; password and account ID are preserved"
            )
        finally:
            subprocess.run(
                [*command, "down", "--volumes", "--remove-orphans"],
                cwd=backend,
                check=True,
                timeout=60,
            )
            print("PASS: Isolated containers, network and test volumes removed")


if __name__ == "__main__":
    check_fresh_environment()
