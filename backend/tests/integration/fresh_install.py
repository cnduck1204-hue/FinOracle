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
    config = yaml.safe_load((backend / "compose.yaml").read_text(encoding="utf-8"))
    config["name"] = project
    # 不占用宿主端口，可与正在运行的开发环境同时检查
    for service in ("mysql", "redis", "gotenberg", "minio", "milvus"):
        config["services"][service].pop("ports", None)
    api = config["services"]["api"]
    api.pop("build", None)
    config["services"]["dsh"].pop("build", None)
    api["ports"] = [
        {"target": 8000, "published": "0", "host_ip": "127.0.0.1", "protocol": "tcp"}
    ]

    with tempfile.TemporaryDirectory(prefix="finoracle-container-test-") as directory:
        path = Path(directory) / "compose.yaml"
        path.write_text(yaml.safe_dump(config), encoding="utf-8")
        # Exercise the documented first-run path with fresh, unrelated credentials.
        sample = Path(directory) / ".env.example"
        sample.write_text(
            (backend / ".env.example").read_text(encoding="utf-8"), encoding="utf-8"
        )
        initialize_env(Path(directory))
        env_file = Path(directory) / ".env"
        settings = Settings(_env_file=env_file)
        admin_password = settings.admin_password.get_secret_value()
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
                timeout=110,
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
    async with SessionFactory() as session:
        assert await session.scalar(select(func.count()).select_from(User).where(User.role == 'admin')) == 1
        admin = await session.scalar(select(User).where(User.role == 'admin'))
        assert admin.password_hash == original and admin.auth_version == 0
    await engine.dispose()
asyncio.run(check())
print('PASS: Initial administrator is created; concurrent/repeated initialization preserves its password')
"""
            print(compose("exec", "-T", "api", "python", "-c", bootstrap_check))
            address = compose("port", "api", "8000").splitlines()[0]
            prefix = settings.api_v1_prefix.rstrip("/")
            with httpx.Client(
                base_url=f"http://{address}{prefix}", timeout=15, trust_env=False
            ) as client:
                login = client.post(
                    "/auth/login",
                    json={
                        "identifier": "admin",
                        "password": admin_password,
                    },
                )
                assert login.status_code == 200
                original_session = login.json()
                original_id = original_session["user"]["id"]

            new_password = "FreshAdmin9" + secrets.token_hex(16)
            env_file.write_text(
                env_file.read_text(encoding="utf-8").replace(
                    f"ADMIN_PASSWORD={admin_password}\n",
                    f"ADMIN_PASSWORD={new_password}\n",
                ),
                encoding="utf-8",
            )
            compose("up", "-d", "--wait", "--wait-timeout", "90", "--no-build")
            address = compose("port", "api", "8000").splitlines()[0]
            with httpx.Client(
                base_url=f"http://{address}{prefix}", timeout=15, trust_env=False
            ) as client:
                stale_login = client.post(
                    "/auth/login",
                    json={"identifier": "admin", "password": admin_password},
                )
                assert stale_login.status_code == 401
                stale_access = client.get(
                    "/users/me",
                    headers={
                        "Authorization": "Bearer " + original_session["access_token"]
                    },
                )
                assert stale_access.status_code == 401
                stale_refresh = client.post(
                    "/auth/refresh",
                    json={"refresh_token": original_session["refresh_token"]},
                )
                assert stale_refresh.status_code == 401

                login = client.post(
                    "/auth/login",
                    json={"identifier": "admin", "password": new_password},
                )
                assert login.status_code == 200
                assert login.json()["user"]["id"] == original_id
                print(
                    "PASS: Redeploying with a new ADMIN_PASSWORD rotates credentials and invalidates existing sessions"
                )

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
            address = compose("port", "api", "8000").splitlines()[0]
            with httpx.Client(
                base_url=f"http://{address}{prefix}", timeout=15, trust_env=False
            ) as client:
                login = client.post(
                    "/auth/login",
                    json={
                        "identifier": "renamed_admin@example.com",
                        "password": new_password,
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
