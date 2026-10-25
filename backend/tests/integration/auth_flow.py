"""Published API regression: python -m tests.integration.auth_flow."""

import asyncio
from uuid import UUID, uuid4

import httpx
from sqlalchemy import delete, select, update

from app.cache.login_failures import LoginFailureStore
from app.cache.redis import redis_client
from app.cache.sessions import SessionStore
from app.core.config import get_settings
from app.db import session as database
from app.models.user import User
from app.security.tokens import decode_token
from tests.integration.containers import compose


async def check_auth_flow():
    settings = get_settings()
    prefix = settings.api_v1_prefix.rstrip("/")
    address = compose("port", "api", "8000").splitlines()[0]
    names = [f"flow_{uuid4().hex[:16]}" for _ in range(2)]
    password = " FinOracle密码123 "
    user_ids, session_keys = [], []
    request_id = f"auth-flow-{uuid4().hex[:12]}"

    async with httpx.AsyncClient(
        base_url=f"http://{address}",
        headers={"X-Request-ID": request_id},
        trust_env=False,
        timeout=12,
    ) as client:

        async def request(method, path, *, status=200, token=None, **kwargs):
            headers = {"Authorization": f"Bearer {token}"} if token else {}
            response = await client.request(
                method, prefix + path, headers=headers, **kwargs
            )
            assert response.status_code == status, (
                f"{method} {path}: expected {status}, got {response.status_code}"
            )
            assert response.headers["X-Request-ID"] == request_id
            if status >= 400:
                assert set(response.json()) == {"code", "message", "request_id"}
                assert response.json()["request_id"] == request_id
            if status == 401:
                assert response.headers["WWW-Authenticate"] == "Bearer"
            if status == 204:
                assert response.content == b""
            return response

        async def login(identifier):
            response = await request(
                "POST",
                "/auth/login",
                json={"identifier": identifier, "password": password},
            )
            result = response.json()
            claims = decode_token(result["access_token"], expected_type="access")
            session_keys.append(SessionStore._key(claims.sid))
            assert response.headers["Cache-Control"] == "no-store"
            return result

        async def refresh(token):
            response = await request(
                "POST", "/auth/refresh", json={"refresh_token": token}
            )
            assert response.headers["Cache-Control"] == "no-store"
            assert response.headers["Pragma"] == "no-cache"
            return response.json()

        try:
            await request("GET", "/ready")
            await request("GET", "/users/me", status=401)
            for username in names:
                response = await request(
                    "POST",
                    "/auth/register",
                    status=201,
                    json={
                        "username": username.upper(),
                        "email": f"{username}@example.com",
                        "password": password,
                        "confirm_password": password,
                    },
                )
                profile = response.json()
                user_ids.append(UUID(profile["id"]))
                assert profile["username"] == username and profile["role"] == "user"
                assert "password_hash" not in profile
                assert profile["created_at"].endswith("Z")
            first, second, other = (
                await login(names[0]),
                await login(f"{names[0]}@example.com"),
                await login(names[1]),
            )
            response = await request("GET", "/users/me", token=first["access_token"])
            # The second login updates this account's timestamps for both devices.
            assert response.json() == second["user"]
            assert response.headers["Cache-Control"] == "no-store"
            await request(
                "POST",
                "/auth/refresh",
                status=422,
                json={"refresh_token": first["refresh_token"], "role": "admin"},
            )
            await request(
                "POST",
                "/auth/refresh",
                status=401,
                json={"refresh_token": first["access_token"]},
            )
            print(
                "PASS: Registration, username/email login, current user and token type/input validation"
            )

            renewed = await refresh(first["refresh_token"])
            old_claims = decode_token(first["refresh_token"], expected_type="refresh")
            new_claims = decode_token(renewed["refresh_token"], expected_type="refresh")
            assert old_claims.sid == new_claims.sid and old_claims.jti != new_claims.jti
            await request("GET", "/users/me", token=renewed["access_token"])
            await request(
                "POST", "/auth/logout", token=renewed["access_token"], status=204
            )
            for access in [first["access_token"], renewed["access_token"]]:
                await request("GET", "/users/me", token=access, status=401)
            await request(
                "POST",
                "/auth/refresh",
                status=401,
                json={"refresh_token": renewed["refresh_token"]},
            )
            await request("GET", "/users/me", token=second["access_token"])
            print(
                "PASS: Refresh and current logout invalidate both token types only for their session"
            )

            await request(
                "POST", "/auth/logout-all", status=204, token=second["access_token"]
            )
            await request("GET", "/users/me", status=401, token=second["access_token"])
            await request(
                "POST",
                "/auth/refresh",
                status=401,
                json={"refresh_token": second["refresh_token"]},
            )
            await request("GET", "/users/me", token=other["access_token"])
            third = await login(names[0])
            renewed = await refresh(third["refresh_token"])
            await request(
                "POST",
                "/auth/refresh",
                status=401,
                json={"refresh_token": third["refresh_token"]},
            )
            await request("GET", "/users/me", status=401, token=renewed["access_token"])
            print(
                "PASS: Logout-all preserves other users; a later login works; refresh replay revokes its session"
            )

            limiter = LoginFailureStore(
                redis_client,
                max_failures=settings.login_max_failures,
                lock_seconds=settings.login_lock_seconds,
            )
            for _ in range(settings.login_max_failures):
                await request(
                    "POST",
                    "/auth/login",
                    status=401,
                    json={"identifier": names[0], "password": "Wrong123"},
                )
            await request(
                "POST",
                "/auth/login",
                status=429,
                json={"identifier": names[0], "password": password},
            )
            await limiter.clear(names[0])
            active = await login(names[0])
            async with database.SessionFactory() as session:
                await session.execute(
                    update(User).where(User.id == user_ids[0]).values(status="disabled")
                )
                await session.commit()
            await request("GET", "/users/me", token=active["access_token"], status=403)
            await request(
                "POST",
                "/auth/refresh",
                status=403,
                json={"refresh_token": active["refresh_token"]},
            )
            print(
                "PASS: Failure limit and live account status are enforced through the published API"
            )
        finally:
            keys = [
                *session_keys,
                *(SessionStore._user_key(user_id) for user_id in user_ids),
            ]
            limiter = LoginFailureStore(redis_client, max_failures=5, lock_seconds=900)
            keys.extend(
                limiter._key(identifier)
                for name in names
                for identifier in [name, f"{name}@example.com"]
            )
            try:
                await redis_client.delete(*keys)
            finally:
                async with database.SessionFactory() as session:
                    await session.execute(delete(User).where(User.username.in_(names)))
                    await session.commit()
            async with database.SessionFactory() as session:
                assert (
                    await session.scalar(select(User).where(User.username.in_(names)))
                    is None
                )
            print("PASS: Test users, session records, indexes and counters removed")


async def main():
    try:
        await asyncio.wait_for(check_auth_flow(), timeout=60)
    finally:
        await redis_client.aclose()
        await database.engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
