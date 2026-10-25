"""Run explicitly with `uv run python -m tests.integration.refresh_transactions` from backend/."""

import asyncio
import sys
from unittest.mock import patch
from uuid import uuid4

from redis.exceptions import RedisError
from sqlalchemy import delete, event, select, update
from sqlalchemy.exc import OperationalError, SQLAlchemyError

from app.cache.login_failures import LoginFailureStore
from app.cache.redis import redis_client
from app.cache.sessions import SessionStore
from app.core.exceptions import AppException
from app.db import session as database
from app.models.user import User
from app.schemas.auth import LoginRequest, LoginResponse, RegisterRequest
from app.security.tokens import decode_token
from app.services.auth import register_user
from app.services.current_user import get_current_user
from app.services.login import login_user
from app.services.refresh import refresh_tokens


async def check_refresh():
    username = f"refresh_{uuid4().hex[:16]}"
    email = f"{username}@example.com"
    password = " FinOracle密码123 "
    session_keys = []
    original_create = SessionStore.create
    store = SessionStore(redis_client)

    async def tracked_create(instance, record):
        session_keys.append(f"finoracle:v2:auth:session:{record.id}")
        session_keys.append(SessionStore._user_key(record.user_id))
        await original_create(instance, record)

    async def log_in():
        async with database.SessionFactory() as session:
            return await login_user(
                LoginRequest(identifier=username, password=password),
                session,
                redis_client,
            )

    async def renew(token):
        async with database.SessionFactory() as session:
            response = await refresh_tokens(token, session, redis_client)
            assert not session.in_transaction()
            return response

    async def resolve(token):
        async with database.SessionFactory() as session:
            return await get_current_user(token, session, redis_client)

    async def expect_error(operation, code, status):
        try:
            await operation
        except AppException as error:
            assert (error.code, error.status_code) == (code, status)
            return error
        raise AssertionError("Authentication unexpectedly succeeded")

    async def load_user():
        async with database.SessionFactory() as session:
            return await session.scalar(select(User).where(User.username == username))

    async def change_user(user_id, **values):
        async with database.SessionFactory() as session:
            await session.execute(
                update(User).where(User.id == user_id).values(**values)
            )
            await session.commit()

    try:
        async with database.SessionFactory() as session:
            await register_user(
                RegisterRequest(
                    username=username,
                    email=email,
                    password=password,
                    confirm_password=password,
                ),
                session,
            )

        with patch.object(SessionStore, "create", tracked_create):
            original = await log_in()
            independent = await log_in()
            old_claims = decode_token(original.refresh_token, expected_type="refresh")
            original_record = await store.get(old_claims.sid)
            await change_user(old_claims.sub, role="admin")
            baseline = await load_user()
            result = await renew(original.refresh_token)
            access = decode_token(result.access_token, expected_type="access")
            refreshed = decode_token(result.refresh_token, expected_type="refresh")
            record = await store.get(refreshed.sid)
            assert access.sid == refreshed.sid == old_claims.sid
            assert len({access.jti, refreshed.jti, old_claims.jti}) == 3
            assert access.role == refreshed.role == result.user.role == "admin"
            assert old_claims.role == "user"
            assert record.refresh_jti == refreshed.jti and not record.revoked
            assert record.created_at == original_record.created_at
            assert record.expires_at == refreshed.exp
            key = f"finoracle:v2:auth:session:{record.id}"
            assert await redis_client.expiretime(key) == refreshed.exp
            raw = await redis_client.get(key)
            assert result.access_token not in raw and result.refresh_token not in raw
            persisted = await load_user()
            assert persisted.last_login_at == baseline.last_login_at
            assert persisted.updated_at == baseline.updated_at
            assert (await resolve(original.access_token)).id == old_claims.sub
            assert (await resolve(result.access_token)).id == old_claims.sub
            print(
                "PASS: Real refresh returns a valid pair with the current role, unchanged login time and matching session expiry"
            )

            latest = await renew(result.refresh_token)
            await expect_error(renew(original.refresh_token), "AUTH_INVALID_TOKEN", 401)
            assert (await store.get(old_claims.sid)).revoked
            for token in [
                original.access_token,
                result.access_token,
                latest.access_token,
            ]:
                await expect_error(resolve(token), "AUTH_INVALID_TOKEN", 401)
            await expect_error(renew(latest.refresh_token), "AUTH_INVALID_TOKEN", 401)
            assert (await resolve(independent.access_token)).id == old_claims.sub
            print(
                "PASS: Reusing an old refresh token invalidates the whole session while another login stays usable"
            )

            independent_claims = decode_token(
                independent.refresh_token, expected_type="refresh"
            )
            independent_key = f"finoracle:v2:auth:session:{independent_claims.sid}"
            before = await redis_client.get(independent_key)
            expiration = await redis_client.expiretime(independent_key)
            await change_user(old_claims.sub, status="disabled")
            await expect_error(renew(independent.refresh_token), "USER_DISABLED", 403)
            assert await redis_client.get(independent_key) == before
            await change_user(old_claims.sub, status="active")

            error = OperationalError(
                "COMMIT", {}, Exception(2006, "injected test failure")
            )
            async with database.SessionFactory() as session:

                def fail_commit(_):
                    raise error

                event.listen(
                    session.sync_session, "before_commit", fail_commit, once=True
                )
                caught = await expect_error(
                    refresh_tokens(independent.refresh_token, session, redis_client),
                    "AUTH_SERVICE_UNAVAILABLE",
                    503,
                )
                assert caught.__cause__ is error and not session.in_transaction()
            assert await redis_client.get(independent_key) == before
            assert await redis_client.expiretime(independent_key) == expiration
            print(
                "PASS: Disabled accounts and DB commit failures consume no refresh token or session TTL"
            )

            original_rotate = SessionStore.rotate
            changed_jtis = []

            async def lose_reply(instance, **arguments):
                outcome = await original_rotate(instance, **arguments)
                assert outcome == "rotated"
                changed_jtis.append(arguments["new_refresh_jti"])
                raise RedisError("Simulated lost reply after the session was rotated")

            with patch.object(SessionStore, "rotate", lose_reply):
                await expect_error(
                    renew(independent.refresh_token), "AUTH_SERVICE_UNAVAILABLE", 503
                )
            revoked = await store.get(independent_claims.sid)
            assert revoked.revoked and revoked.refresh_jti == changed_jtis[0]
            await expect_error(
                resolve(independent.access_token), "AUTH_INVALID_TOKEN", 401
            )
            print(
                "PASS: A lost reply after a real Redis rotation returns 503 and revokes the changed session"
            )

            race = await log_in()
            outcomes = await asyncio.gather(
                *(renew(race.refresh_token) for _ in range(4)), return_exceptions=True
            )
            winners = [
                outcome for outcome in outcomes if isinstance(outcome, LoginResponse)
            ]
            losers = [
                outcome for outcome in outcomes if isinstance(outcome, AppException)
            ]
            assert len(winners) == 1 and len(losers) == 3
            assert all(
                (error.code, error.status_code) == ("AUTH_INVALID_TOKEN", 401)
                for error in losers
            )
            race_claims = decode_token(race.refresh_token, expected_type="refresh")
            assert (await store.get(race_claims.sid)).revoked
            await expect_error(
                resolve(winners[0].access_token), "AUTH_INVALID_TOKEN", 401
            )
            print(
                "PASS: Concurrent real DB/Redis refresh requests produce one response and revoke the session on replay"
            )
    finally:
        limiter = LoginFailureStore(redis_client, max_failures=5, lock_seconds=900)
        keys = [*session_keys, limiter._key(username), limiter._key(email)]
        try:
            await redis_client.delete(*keys)
            assert await redis_client.exists(*keys) == 0
        finally:
            async with database.SessionFactory() as session:
                await session.execute(delete(User).where(User.username == username))
                await session.commit()
        assert await load_user() is None
        print(
            "PASS: Only this run's test user, sessions and failure counters were deleted"
        )


async def main():
    try:
        await asyncio.wait_for(check_refresh(), timeout=40)
    finally:
        try:
            await redis_client.aclose()
        finally:
            await database.engine.dispose()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (
        AssertionError,
        RedisError,
        SQLAlchemyError,
        AppException,
        TimeoutError,
    ) as error:
        print(f"FAIL: {type(error).__name__}")
        sys.exit(1)
