"""Sequential HTTP latency probe; creates disposable users on a local API only."""

import argparse
import asyncio
import json
import math
import os
import platform
import re
import secrets
import sys
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from urllib.parse import urlsplit
from uuid import UUID

import httpx


def cleanup_generated(users: list[tuple[str, str | None]], admin_access: str | None):
    """Remove only this run's exact generated identities and their session records."""
    if not users and not admin_access:
        return
    if any(not re.fullmatch(r"probe_[0-9a-f]{20}", name) for name, _ in users):
        raise RuntimeError("Cleanup refused an unexpected account name")

    async def cleanup():
        from sqlalchemy import delete, select

        from app.cache.redis import redis_client
        from app.cache.sessions import SessionStore
        from app.db.session import SessionFactory, engine
        from app.models.user import User
        from app.security.tokens import decode_token

        expected = {name: UUID(user_id) if user_id else None for name, user_id in users}
        try:
            async with SessionFactory() as session:
                rows = (
                    await session.scalars(
                        select(User).where(User.username.in_(expected))
                    )
                ).all()
                if not {user_id for user_id in expected.values() if user_id} <= {
                    user.id for user in rows
                }:
                    raise RuntimeError("Cleanup database differs from the target API")
                for user in rows:
                    if user.role != "user" or expected[user.username] not in {
                        None,
                        user.id,
                    }:
                        raise RuntimeError("Cleanup account identity mismatch")
                owned_ids = {str(user.id) for user in rows}
                try:
                    async for key in redis_client.scan_iter(
                        match="finoracle:v2:auth:session:*", count=100
                    ):
                        raw = await redis_client.get(key)
                        if raw and json.loads(raw).get("user_id") in owned_ids:
                            await redis_client.eval(
                                "if redis.call('GET', KEYS[1]) == ARGV[1] then "
                                "return redis.call('DEL', KEYS[1]) end return 0",
                                1,
                                key,
                                raw,
                            )
                    if admin_access:
                        claims = decode_token(admin_access, expected_type="access")
                        await redis_client.delete(SessionStore._key(claims.sid))
                finally:
                    await session.execute(
                        delete(User).where(
                            User.id.in_([user.id for user in rows]),
                            User.username.in_(expected),
                            User.role == "user",
                        )
                    )
                    await session.commit()
        finally:
            await redis_client.aclose()
            await engine.dispose()

    try:
        asyncio.run(cleanup())
    except Exception:
        raise RuntimeError(
            "Cleanup failed; inspect only this run's generated accounts: "
            + ", ".join(name for name, _ in users)
        ) from None


def validate_target(url: str, allow_disposable_users: bool) -> None:
    target = urlsplit(url)
    if not allow_disposable_users:
        raise ValueError("Pass --allow-disposable-users to create local test accounts")
    if (
        target.scheme not in {"http", "https"}
        or target.hostname not in {"localhost", "127.0.0.1", "::1"}
        or target.username is not None
        or target.password is not None
        or target.query
        or target.fragment
    ):
        raise ValueError("Target must be a loopback URL without credentials or query")


def summarize(timings: dict[str, list[float]], threshold: float | None) -> dict:
    result = {}
    for name, values in sorted(timings.items()):
        ordered = sorted(values)
        p95 = ordered[math.ceil(len(ordered) * 0.95) - 1]
        result[name] = {
            "samples": len(ordered),
            "p50_ms": round(ordered[math.ceil(len(ordered) * 0.5) - 1], 3),
            "p95_ms": round(p95, 3),
            "within_goal": p95 <= 500,
            "within_threshold": threshold is None or p95 <= threshold,
        }
    return result


def run_probe(
    client: httpx.Client,
    *,
    samples: int,
    warmup: int,
    admin_identifier: str | None = None,
    admin_password: str | None = None,
) -> dict[str, list[float]]:
    if samples < 1 or warmup < 0:
        raise ValueError("samples must be positive and warmup must be nonnegative")
    if bool(admin_identifier) != bool(admin_password):
        raise ValueError("Admin identifier and password must be supplied together")
    timings = defaultdict(list)
    measured = False

    def request(name, method, path, expected=200, **kwargs):
        started = perf_counter()
        response = client.request(method, path, **kwargs)
        elapsed = (perf_counter() - started) * 1000
        if response.status_code != expected:
            raise RuntimeError(f"{name} returned HTTP {response.status_code}")
        if measured:
            timings[name].append(elapsed)
        return response.json() if response.content else None

    def bearer(tokens):
        return {"Authorization": f"Bearer {tokens['access_token']}"}

    admin = None
    generated = []
    try:
        if admin_identifier:
            admin = request(
                "admin_login",
                "POST",
                "auth/login",
                json={"identifier": admin_identifier, "password": admin_password},
            )
        for index in range(warmup + samples):
            measured = index >= warmup
            username = "probe_" + secrets.token_hex(10)
            generated.append((username, None))
            password = "Probe9" + secrets.token_urlsafe(24)
            user = request(
                "register",
                "POST",
                "auth/register",
                expected=201,
                json={
                    "username": username,
                    "email": f"{username}@example.com",
                    "password": password,
                    "confirm_password": password,
                },
            )
            generated[-1] = (username, user["id"])
            login = {"identifier": username, "password": password}
            tokens = request("login", "POST", "auth/login", json=login)
            request("me", "GET", "users/me", headers=bearer(tokens))
            request(
                "patch_me",
                "PATCH",
                "users/me",
                headers=bearer(tokens),
                json={"email": f"{username}_updated@example.com"},
            )
            tokens = request(
                "refresh",
                "POST",
                "auth/refresh",
                json={"refresh_token": tokens["refresh_token"]},
            )
            request(
                "logout", "POST", "auth/logout", expected=204, headers=bearer(tokens)
            )
            if admin:
                headers = bearer(admin)
                user_path = f"admin/users/{user['id']}"
                request(
                    "admin_list",
                    "GET",
                    "admin/users",
                    headers=headers,
                    params={"q": username, "page": 1, "page_size": 20},
                )
                request("admin_detail", "GET", user_path, headers=headers)
                for status in ("disabled", "active"):
                    request(
                        "admin_disable" if status == "disabled" else "admin_enable",
                        "PATCH",
                        user_path + "/status",
                        headers=headers,
                        json={"status": status},
                    )
    finally:
        measured = False
        try:
            if admin:
                request(
                    "admin_logout",
                    "POST",
                    "auth/logout",
                    expected=204,
                    headers=bearer(admin),
                )
        finally:
            cleanup_generated(generated, admin["access_token"] if admin else None)
    return dict(timings)


def main() -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8001/api/v1")
    parser.add_argument("--allow-disposable-users", action="store_true")
    parser.add_argument("--samples", type=int, default=20)
    parser.add_argument("--warmup", type=int, default=3)
    parser.add_argument("--environment", default="local-compose")
    parser.add_argument("--max-p95-ms", type=float)
    args = parser.parse_args()
    try:
        validate_target(args.base_url, args.allow_disposable_users)
        if args.max_p95_ms is not None and (
            not math.isfinite(args.max_p95_ms) or args.max_p95_ms <= 0
        ):
            raise ValueError("--max-p95-ms must be finite and positive")
        with httpx.Client(
            base_url=args.base_url.rstrip("/") + "/",
            timeout=10,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            timings = run_probe(
                client,
                samples=args.samples,
                warmup=args.warmup,
                admin_identifier=os.environ.get("BENCHMARK_ADMIN_IDENTIFIER"),
                admin_password=os.environ.get("BENCHMARK_ADMIN_PASSWORD"),
            )
        endpoints = summarize(timings, args.max_p95_ms)
        print(
            json.dumps(
                {
                    "measured_at": datetime.now(UTC).isoformat(),
                    "environment": args.environment,
                    "platform": platform.platform(),
                    "python": platform.python_version(),
                    "base_url": args.base_url,
                    "concurrency": 1,
                    "warmup_per_endpoint": args.warmup,
                    "disposable_users_created": args.samples + args.warmup,
                    "goal_p95_ms": 500,
                    "threshold_p95_ms": args.max_p95_ms,
                    "endpoints": endpoints,
                },
                indent=2,
            )
        )
        return 0 if all(row["within_threshold"] for row in endpoints.values()) else 1
    except (ValueError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
    except (httpx.HTTPError, KeyError, TypeError):
        print("Probe failed: connection or response contract error", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
