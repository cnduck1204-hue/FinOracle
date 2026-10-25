import asyncio
from time import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import event
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.exceptions import AppException
from app.models.user import User, utc_now
from app.schemas.session import SessionRecord
from app.security import tokens
from app.security.tokens import create_token, decode_token
from app.services import current_user

TEST_KEY = "d98f15c0e2b64aa7b91d0438f5c6e270a18b3f9d42e65c07b6a1d8e34f9205ac"


@pytest.fixture
def current_context(monkeypatch):
    settings = Settings(_env_file=None, jwt_secret_key=TEST_KEY)
    monkeypatch.setattr(tokens, "get_settings", lambda: settings)
    user = User(
        id=uuid4(),
        username="alice_01",
        email="alice@example.com",
        password_hash="private-hash-marker",
        role="admin",
        status="active",
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    token = create_token(
        user_id=user.id,
        role="user",
        session_id=uuid4(),
        token_id=uuid4(),
        token_type="access",
    )
    claims = decode_token(token, expected_type="access")
    record = SessionRecord(
        id=claims.sid,
        user_id=user.id,
        refresh_jti=uuid4(),
        created_at=claims.iat,
        expires_at=claims.exp + 3600,
    )
    cache = Mock(spec=Redis)
    cache.get = AsyncMock(return_value=record.model_dump_json())
    session = AsyncSession(expire_on_commit=False)
    transactions = []
    event.listen(
        session.sync_session, "after_commit", lambda _: transactions.append("commit")
    )
    event.listen(
        session.sync_session,
        "after_rollback",
        lambda _: transactions.append("rollback"),
    )

    async def lookup_user(user_id):
        assert session.in_transaction()
        return user

    lookup = AsyncMock(side_effect=lookup_user)
    factory = Mock(return_value=SimpleNamespace(get_by_id=lookup))
    monkeypatch.setattr(current_user, "UserRepository", factory)
    yield SimpleNamespace(
        user=user,
        token=token,
        claims=claims,
        record=record,
        cache=cache,
        session=session,
        lookup=lookup,
        factory=factory,
        transactions=transactions,
    )
    asyncio.run(session.close())


def resolve(context, token=None):
    return asyncio.run(
        current_user.get_current_user(
            context.token if token is None else token, context.session, context.cache
        )
    )


def test_valid_access_returns_latest_database_user_and_role(current_context):
    context = current_context
    original_times = (context.user.last_login_at, context.user.updated_at)

    result = resolve(context)

    assert result is context.user
    assert context.claims.role == "user" and result.role == "admin"
    assert context.claims.jti != context.record.refresh_jti
    context.cache.get.assert_awaited_once_with(
        f"finoracle:v2:auth:session:{context.claims.sid}"
    )
    context.lookup.assert_awaited_once_with(context.claims.sub)
    context.factory.assert_called_once_with(context.session)
    assert (result.last_login_at, result.updated_at) == original_times
    assert context.transactions == ["commit"]
    assert not context.session.in_transaction()


@pytest.mark.parametrize("mode", ["missing", "revoked", "other-user", "expired"])
def test_invalid_session_is_rejected_before_loading_user(current_context, mode):
    context = current_context
    if mode == "missing":
        context.cache.get.return_value = None
    else:
        data = context.record.model_dump(mode="json")
        if mode == "revoked":
            data["revoked"] = True
        elif mode == "other-user":
            data["user_id"] = str(uuid4())
        else:
            data["created_at"] = int(time()) - 60
            data["expires_at"] = int(time()) - 1
        context.cache.get.return_value = SessionRecord.model_validate(
            data
        ).model_dump_json()

    with pytest.raises(AppException) as caught:
        resolve(context)

    assert (caught.value.code, caught.value.status_code) == ("AUTH_INVALID_TOKEN", 401)
    context.lookup.assert_not_awaited()
    assert context.transactions == []


@pytest.mark.parametrize("mode", ["malformed", "refresh"])
def test_invalid_or_refresh_token_never_reads_redis_or_database(current_context, mode):
    context = current_context
    token = "invalid-token-marker"
    if mode == "refresh":
        token = create_token(
            user_id=context.user.id,
            role="user",
            session_id=context.claims.sid,
            token_id=context.record.refresh_jti,
            token_type="refresh",
        )

    with pytest.raises(AppException) as caught:
        resolve(context, token)

    assert (caught.value.code, caught.value.status_code) == ("AUTH_INVALID_TOKEN", 401)
    context.cache.get.assert_not_awaited()
    context.lookup.assert_not_awaited()


@pytest.mark.parametrize("mode", ["redis-error", "invalid-json", "wrong-id"])
def test_unavailable_or_corrupt_session_data_returns_503(current_context, mode):
    context = current_context
    if mode == "redis-error":
        context.cache.get.side_effect = RedisError("private-redis-error-marker")
    elif mode == "invalid-json":
        context.cache.get.return_value = "{"
    else:
        context.cache.get.return_value = context.record.model_copy(
            update={"id": uuid4()}
        ).model_dump_json()

    with pytest.raises(AppException) as caught:
        resolve(context)

    assert (caught.value.code, caught.value.status_code) == (
        "AUTH_SERVICE_UNAVAILABLE",
        503,
    )
    assert caught.value.__cause__ is not None
    context.lookup.assert_not_awaited()
    assert not context.session.in_transaction()


@pytest.mark.parametrize("mode", ["deleted", "disabled"])
def test_database_user_state_is_checked_on_every_request(current_context, mode):
    context = current_context
    if mode == "deleted":
        context.lookup.side_effect = None
        context.lookup.return_value = None
    else:
        context.user.status = "disabled"

    with pytest.raises(AppException) as caught:
        resolve(context)

    expected = (
        ("AUTH_INVALID_TOKEN", 401) if mode == "deleted" else ("USER_DISABLED", 403)
    )
    assert (caught.value.code, caught.value.status_code) == expected
    assert context.transactions == ["commit"]
    assert not context.session.in_transaction()


@pytest.mark.parametrize("phase", ["lookup", "commit"])
def test_database_outage_returns_503_and_closes_transaction(current_context, phase):
    context = current_context
    error = OperationalError("SELECT", {}, Exception(2006, "private-db-error-marker"))
    if phase == "lookup":
        context.lookup.side_effect = error
    else:

        def fail_commit(_):
            raise error

        event.listen(context.session.sync_session, "before_commit", fail_commit)

    with pytest.raises(AppException) as caught:
        resolve(context)

    assert (caught.value.code, caught.value.status_code) == (
        "AUTH_SERVICE_UNAVAILABLE",
        503,
    )
    assert caught.value.__cause__ is error
    assert context.transactions == ["rollback"]
    assert not context.session.in_transaction()


def test_unexpected_lookup_bug_is_not_misreported_as_bad_token(current_context):
    context = current_context
    error = RuntimeError("unexpected-programming-error")
    context.lookup.side_effect = error

    with pytest.raises(RuntimeError) as caught:
        resolve(context)

    assert caught.value is error
    assert context.transactions == ["rollback"]
