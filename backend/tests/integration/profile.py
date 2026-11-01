"""Published profile API and real transaction checks: python -m tests.integration.profile."""

import asyncio
from unittest.mock import patch
from uuid import UUID, uuid4

import httpx
from sqlalchemy import delete, select

from app.cache.login_failures import LoginFailureStore
from app.cache.redis import redis_client
from app.cache.sessions import SessionStore
from app.core.config import get_settings
from app.core.exceptions import AppException
from app.db import session as database
from app.models.user import User
from app.repositories.user import UserRepository
from app.schemas.profile import ProfileUpdateRequest
from app.security.tokens import decode_token
from app.services.profile import update_profile
from tests.integration.containers import compose


async def check_profile():
    prefix = f"prof_{uuid4().hex[:12]}"
    names = [f"{prefix}_{index}" for index in range(3)]
    changed_name = f"{prefix}_new"
    changed_email = f"{prefix}_new@example.com"
    race_email = f"{prefix}_race@example.com"
    user_ids, session_keys = [], []
    password = "ProfileTest123"
    api_prefix = get_settings().api_v1_prefix.rstrip("/")
    address = compose("port", "api", "8000").splitlines()[0]

    async with httpx.AsyncClient(
        base_url=f"http://{address}{api_prefix}", timeout=15, trust_env=False
    ) as client:

        async def request(method, path, *, status=200, token=None, **kwargs):
            headers = {"Authorization": f"Bearer {token}"} if token else {}
            response = await client.request(method, path, headers=headers, **kwargs)
            assert response.status_code == status, (
                f"{method} {path}: expected {status}, got {response.status_code}"
            )
            assert (
                password not in response.text and "password_hash" not in response.text
            )
            return response

        try:
            for name in names:
                response = await request(
                    "POST",
                    "/auth/register",
                    status=201,
                    json={
                        "username": name,
                        "email": f"{name}@example.com",
                        "password": password,
                        "confirm_password": password,
                    },
                )
                user_ids.append(UUID(response.json()["id"]))

            response = await request(
                "POST",
                "/auth/login",
                json={"identifier": names[0], "password": password},
            )
            login = response.json()
            token = login["access_token"]
            claims = decode_token(token, expected_type="access")
            session_keys.append(SessionStore._key(claims.sid))
            await request(
                "PATCH", "/users/me", status=401, json={"username": changed_name}
            )

            response = await request(
                "PATCH",
                "/users/me",
                token=token,
                json={"username": f" {changed_name.upper()} "},
            )
            profile = response.json()
            assert profile["id"] == str(user_ids[0])
            assert profile["username"] == changed_name
            assert profile["email"] == f"{names[0]}@example.com"
            assert profile["role"] == "user" and profile["status"] == "active"
            assert profile["last_login_at"] == login["user"]["last_login_at"]
            assert profile["updated_at"] > login["user"]["updated_at"]
            assert profile["updated_at"].endswith("Z")
            assert response.headers["Cache-Control"] == "no-store"

            response = await request(
                "PATCH",
                "/users/me",
                token=token,
                json={"username": changed_name, "email": f"{names[0]}@example.com"},
            )
            assert response.json() == profile

            for body, code in [
                ({"username": names[1]}, "USER_USERNAME_EXISTS"),
                ({"email": f"{names[1]}@example.com"}, "USER_EMAIL_EXISTS"),
            ]:
                response = await request(
                    "PATCH", "/users/me", token=token, status=409, json=body
                )
                assert response.json()["code"] == code

            for body in [
                {},
                {"email": None},
                {"username": "ab"},
                {"username": changed_name, "role": "admin"},
            ]:
                await request("PATCH", "/users/me", token=token, status=422, json=body)
            await request(
                "PATCH", "/users/me", token=token, status=422, content='{"username":'
            )
            current = await request("GET", "/users/me", token=token)
            assert current.json() == profile
            print(
                "PASS: Profile API applies normalization, partial updates, ownership, safe fields and input/conflict checks"
            )

            # Both sessions hold an old identity-map copy, as authentication does.
            async with (
                database.SessionFactory() as first,
                database.SessionFactory() as second,
            ):
                stale_first = await first.get(User, user_ids[0])
                stale_second = await second.get(User, user_ids[0])
                await first.commit()
                await second.commit()
                assert stale_first.email == stale_second.email
                await asyncio.gather(
                    update_profile(
                        ProfileUpdateRequest(username=f"{prefix}_last"),
                        user_ids[0],
                        first,
                    ),
                    update_profile(
                        ProfileUpdateRequest(email=changed_email), user_ids[0], second
                    ),
                )
            async with database.SessionFactory() as session:
                stored = await session.get(User, user_ids[0])
                assert (
                    stored.username == f"{prefix}_last"
                    and stored.email == changed_email
                )
                assert stored.role == "user" and stored.status == "active"
            print(
                "PASS: Concurrent partial updates reload locked rows and preserve both changes"
            )

            barrier = asyncio.Barrier(2)
            original_lookup = UserRepository.get_by_email

            async def synchronized_lookup(repository, email):
                existing = await original_lookup(repository, email)
                if email == race_email:
                    assert existing is None
                    await asyncio.wait_for(barrier.wait(), timeout=10)
                return existing

            async def change_email(user_id):
                async with database.SessionFactory() as session:
                    try:
                        await update_profile(
                            ProfileUpdateRequest(email=race_email), user_id, session
                        )
                    except AppException as error:
                        assert (
                            error.code == "USER_ALREADY_EXISTS"
                            and error.status_code == 409
                        )
                        return "conflict"
                    assert not session.in_transaction()
                    return "updated"

            with patch.object(UserRepository, "get_by_email", synchronized_lookup):
                results = await asyncio.gather(
                    *(change_email(user_id) for user_id in user_ids[1:])
                )
            assert sorted(results) == ["conflict", "updated"]
            async with database.SessionFactory() as session:
                rows = (
                    await session.scalars(select(User).where(User.id.in_(user_ids[1:])))
                ).all()
                assert sum(user.email == race_email for user in rows) == 1
            print(
                "PASS: Concurrent unique-email updates commit once and return a safe 409 once"
            )
        finally:
            limiter = LoginFailureStore(redis_client, max_failures=5, lock_seconds=900)
            keys = session_keys + [limiter._key(name) for name in names]
            try:
                if keys:
                    await redis_client.delete(*keys)
            finally:
                async with database.SessionFactory() as session:
                    # IDs remain ours even after usernames/emails change.
                    await session.execute(delete(User).where(User.id.in_(user_ids)))
                    await session.commit()
            async with database.SessionFactory() as session:
                assert (
                    await session.scalar(select(User.id).where(User.id.in_(user_ids)))
                    is None
                )
            print("PASS: Only this run's generated users and session data were removed")


async def main():
    try:
        await asyncio.wait_for(check_profile(), timeout=60)
    finally:
        await redis_client.aclose()
        await database.engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
