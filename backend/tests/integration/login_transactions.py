"""Run explicitly with `uv run python -m tests.integration.login_transactions` from backend/."""

import asyncio
import sys
from unittest.mock import patch
from uuid import uuid4

from redis.exceptions import RedisError
from sqlalchemy import delete, event, select
from sqlalchemy.exc import OperationalError, SQLAlchemyError

from app.cache.login_failures import LoginFailureStore
from app.cache.redis import redis_client
from app.cache.sessions import SessionStore
from app.core.exceptions import AppException
from app.db import session as database
from app.models.user import User
from app.schemas.auth import LoginRequest, RegisterRequest
from app.security.tokens import decode_token
from app.services import login
from app.services.auth import register_user


async def check_login():
    username = f"login_{uuid4().hex[:16]}"
    email = f"{username}@example.com"
    password = " FinOracle密码123 "
    payload = LoginRequest(identifier=f" {username.upper()} ", password=password)
    records = []
    original_create = SessionStore.create
    failure = {"mode": None, "session": None}
    database_error = OperationalError(
        "COMMIT", {}, Exception(2006, "injected test failure")
    )

    async def tracked_create(store, record):
        records.append(record)
        await original_create(store, record)
        if failure["mode"] == "commit":

            def fail_commit(_):
                raise database_error

            event.listen(
                failure["session"].sync_session, "before_commit", fail_commit, once=True
            )
        elif failure["mode"] == "redis":
            raise RedisError("Simulated lost reply after session was written")

    store = SessionStore(redis_client)

    async def load_user():
        async with database.SessionFactory() as session:
            return await session.scalar(select(User).where(User.username == username))

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
            async with database.SessionFactory() as session:
                response = await login.login_user(payload, session, redis_client)
                assert not session.in_transaction()
            access = decode_token(response.access_token, expected_type="access")
            refresh = decode_token(response.refresh_token, expected_type="refresh")
            saved = await store.get(refresh.sid)
            assert access.sid == refresh.sid == saved.id
            assert access.sub == refresh.sub == saved.user_id
            assert access.jti != refresh.jti == saved.refresh_jti
            assert saved.created_at == refresh.iat
            assert saved.expires_at == refresh.exp
            assert saved.revoked is False
            session_key = f"finoracle:v2:auth:session:{saved.id}"
            assert await redis_client.expiretime(session_key) == refresh.exp
            raw = await redis_client.get(session_key)
            assert (
                response.access_token not in raw and response.refresh_token not in raw
            )
            user = await load_user()
            assert user.last_login_at == response.user.last_login_at.replace(
                tzinfo=None
            )
            assert user.updated_at == response.user.updated_at.replace(tzinfo=None)
            baseline_login = user.last_login_at
            baseline_update = user.updated_at
            print(
                "PASS: Real login returns valid tokens and commits login time plus a Redis session"
            )

            for mode in ["commit", "redis"]:
                failure["mode"] = mode
                before_count = len(records)
                async with database.SessionFactory() as session:
                    failure["session"] = session
                    try:
                        await login.login_user(payload, session, redis_client)
                    except AppException as error:
                        assert error.code == "AUTH_SERVICE_UNAVAILABLE"
                        assert error.status_code == 503
                        if mode == "commit":
                            assert error.__cause__ is database_error
                    else:
                        raise AssertionError("Injected failure did not stop login")
                    assert not session.in_transaction()
                assert len(records) == before_count + 1
                revoked = await store.get(records[-1].id)
                assert revoked is not None and revoked.revoked is True
                assert revoked.expires_at == records[-1].expires_at
                user = await load_user()
                assert user.last_login_at == baseline_login
                assert user.updated_at == baseline_update
                print(
                    f"PASS: {mode} failure rolls back MySQL and revokes the written session"
                )
            failure["mode"] = None

            original_authenticate = login.authenticate_with_login_limit

            async def disable_after_authentication(*args):
                authenticated = await original_authenticate(*args)
                async with database.SessionFactory() as other_session:
                    current = await other_session.get(User, authenticated.id)
                    current.status = "disabled"
                    await other_session.commit()
                return authenticated

            before_count = len(records)
            with patch.object(
                login, "authenticate_with_login_limit", disable_after_authentication
            ):
                async with database.SessionFactory() as session:
                    try:
                        await login.login_user(payload, session, redis_client)
                    except AppException as error:
                        assert (
                            error.code == "USER_DISABLED" and error.status_code == 403
                        )
                    else:
                        raise AssertionError("A concurrently disabled user logged in")
            assert len(records) == before_count
            assert (await load_user()).last_login_at == baseline_login
            print(
                "PASS: Fresh locked lookup rejects an account disabled after password verification"
            )
    finally:
        keys = [f"finoracle:v2:auth:session:{record.id}" for record in records]
        keys.extend({SessionStore._user_key(record.user_id) for record in records})
        limiter = LoginFailureStore(redis_client, max_failures=5, lock_seconds=900)
        keys.extend([limiter._key(username), limiter._key(email)])
        try:
            await redis_client.delete(*keys)
            assert await redis_client.exists(*keys) == 0
        finally:
            async with database.SessionFactory() as session:
                await session.execute(delete(User).where(User.username == username))
                await session.commit()
        assert await load_user() is None
        print("PASS: Only this run's user and Redis records were deleted")


async def main():
    try:
        await asyncio.wait_for(check_login(), timeout=30)
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
