"""Real administrator APIs; optionally record the disposable local latency probe."""

import argparse
import asyncio
import json
import os
import secrets
import subprocess
import sys
from pathlib import Path
from uuid import UUID, uuid4

import httpx
from sqlalchemy import delete

from app.cache.redis import redis_client
from app.cache.sessions import SessionStore
from app.core.config import get_settings
from app.db import session as database
from app.models.user import User
from app.security.passwords import hash_password
from app.security.tokens import decode_token
from tests.integration.containers import compose


async def check_admin(report: Path | None = None):
    prefix = f"admtest_{uuid4().hex[:12]}"
    names = [f"{prefix}_{index}" for index in range(3)]
    password = "AdminTest9" + secrets.token_hex(12)
    ids, keys = [], []
    address = compose("port", "api", "8000").splitlines()[0]
    base_url = f"http://{address}{get_settings().api_v1_prefix.rstrip('/')}"

    async with httpx.AsyncClient(
        base_url=base_url, timeout=15, trust_env=False
    ) as client:

        async def request(method, path, *, expected=200, access=None, **kwargs):
            headers = {"Authorization": f"Bearer {access}"} if access else {}
            response = await client.request(method, path, headers=headers, **kwargs)
            assert response.status_code == expected, (
                f"{method} {path}: expected {expected}, got {response.status_code}"
            )
            assert (
                "password_hash" not in response.text
                and "auth_version" not in response.text
            )
            return response

        async def login(name):
            result = (
                await request(
                    "POST",
                    "/auth/login",
                    json={"identifier": name, "password": password},
                )
            ).json()
            claims = decode_token(result["access_token"], expected_type="access")
            keys.append(SessionStore._key(claims.sid))
            return result

        async def renew(pair, expected=200):
            return await request(
                "POST",
                "/auth/refresh",
                expected=expected,
                json={"refresh_token": pair["refresh_token"]},
            )

        try:
            for name in names:
                user = (
                    await request(
                        "POST",
                        "/auth/register",
                        expected=201,
                        json={
                            "username": name,
                            "email": f"{name}@example.com",
                            "password": password,
                            "confirm_password": password,
                        },
                    )
                ).json()
                ids.append(UUID(user["id"]))
            # This run creates its own administrator, never changes an existing account.
            async with database.SessionFactory() as session:
                async with session.begin():
                    administrator = User(
                        username=f"{prefix}_admin",
                        email=f"{prefix}_admin@example.com",
                        role="admin",
                        status="active",
                        password_hash=await hash_password(password),
                    )
                    session.add(administrator)
                    await session.flush()
                    ids.append(administrator.id)
            admin_pair, regular = await login(f"{prefix}_admin"), await login(names[0])
            access = admin_pair["access_token"]
            await request("GET", "/admin/users", expected=401)
            for method, path, body in [
                ("GET", "/admin/users", None),
                ("GET", f"/admin/users/{ids[0]}", None),
                ("PATCH", f"/admin/users/{ids[0]}/status", {"status": "disabled"}),
            ]:
                await request(
                    method,
                    path,
                    access=regular["access_token"],
                    json=body,
                    expected=403,
                )
            for bad in ["page=0", "page_size=101", "role=root", "status=deleted"]:
                await request("GET", "/admin/users?" + bad, access=access, expected=422)
            listing = (
                await request(
                    "GET",
                    "/admin/users",
                    access=access,
                    params={
                        "q": prefix,
                        "role": "user",
                        "status": "active",
                        "page_size": 2,
                    },
                )
            ).json()
            assert listing["total"] == 3 and len(listing["items"]) == 2
            second = (
                await request(
                    "GET",
                    "/admin/users",
                    access=access,
                    params={
                        "q": prefix,
                        "role": "user",
                        "page_size": 2,
                        "page": 2,
                    },
                )
            ).json()
            assert len(second["items"]) == 1
            assert not {user["id"] for user in listing["items"]} & {
                user["id"] for user in second["items"]
            }
            literal = (
                await request(
                    "GET", "/admin/users", access=access, params={"q": prefix + "%"}
                )
            ).json()
            assert literal["total"] == 0
            await request("GET", f"/admin/users/{uuid4()}", access=access, expected=404)
            await request(
                "PATCH",
                f"/admin/users/{ids[-1]}/status",
                access=access,
                json={"status": "disabled"},
                expected=403,
            )
            print(
                "PASS: Database administrator authorization, bounded filters/pagination, literal search and protected administrators"
            )

            # Keep old-format active sessions compatible through the migration.
            raw = json.loads(await redis_client.get(keys[1]))
            raw.pop("auth_version", None)
            await redis_client.set(keys[1], json.dumps(raw), keepttl=True)
            await request("GET", "/users/me", access=regular["access_token"])
            another, unrelated = await login(names[0]), await login(names[1])
            path = f"/admin/users/{ids[0]}/status"
            for _ in range(2):
                await request("PATCH", path, access=access, json={"status": "disabled"})
            async with database.SessionFactory() as session:
                assert (await session.get(User, ids[0])).auth_version == 1
            await request(
                "GET", "/users/me", access=regular["access_token"], expected=403
            )
            await renew(regular, expected=403)
            await request(
                "POST",
                "/auth/login",
                json={"identifier": names[0], "password": password},
                expected=403,
            )
            await request("PATCH", path, access=access, json={"status": "active"})
            for pair in [regular, another]:
                await request(
                    "GET", "/users/me", access=pair["access_token"], expected=401
                )
                await renew(pair, expected=401)
            await request("GET", "/users/me", access=unrelated["access_token"])
            fresh = await login(names[0])
            await renew(fresh)
            print(
                "PASS: Legacy sessions work; disable is idempotent; re-enable rejects both old token types across logins while unrelated users remain active"
            )

            if report is not None:
                env = {
                    **os.environ,
                    "BENCHMARK_ADMIN_IDENTIFIER": f"{prefix}_admin",
                    "BENCHMARK_ADMIN_PASSWORD": password,
                }
                result = await asyncio.to_thread(
                    subprocess.run,
                    [
                        sys.executable,
                        "scripts/benchmark.py",
                        "--base-url",
                        base_url,
                        "--allow-disposable-users",
                        "--samples",
                        "20",
                        "--warmup",
                        "3",
                        "--environment",
                        "windows-docker-desktop-local",
                        "--max-p95-ms",
                        "500",
                    ],
                    capture_output=True,
                    text=True,
                    env=env,
                    timeout=120,
                )
                if result.stdout.strip():
                    data = json.loads(result.stdout)
                    report.parent.mkdir(parents=True, exist_ok=True)
                    report.write_text(json.dumps(data, indent=2), encoding="utf-8")
                    print(
                        "Performance P95 ms: "
                        + ", ".join(
                            f"{key}={value['p95_ms']}"
                            for key, value in data["endpoints"].items()
                        )
                    )
                assert result.returncode == 0, (
                    "Performance probe failed: " + result.stderr
                )
                print(
                    "PASS: Authentication and user-management P95 are below 500ms in the recorded local serial baseline"
                )
        finally:
            try:
                if keys:
                    await redis_client.delete(*keys)
            finally:
                async with database.SessionFactory() as session:
                    await session.execute(delete(User).where(User.id.in_(ids)))
                    await session.commit()
            print(
                "PASS: Only this run's generated administrator, users and sessions were removed"
            )


async def main(report=None):
    try:
        await asyncio.wait_for(check_admin(report), timeout=180)
    finally:
        await redis_client.aclose()
        await database.engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark-report", type=Path)
    asyncio.run(main(parser.parse_args().benchmark_report))
