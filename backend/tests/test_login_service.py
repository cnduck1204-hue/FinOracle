import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from redis.exceptions import RedisError
from sqlalchemy import event
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.exceptions import AppException
from app.models.user import User, utc_now
from app.schemas.auth import LoginRequest
from app.security import tokens
from app.security.tokens import decode_token
from app.services import login

TEST_KEY = "d98f15c0e2b64aa7b91d0438f5c6e270a18b3f9d42e65c07b6a1d8e34f9205ac"


@pytest.fixture
def login_context(monkeypatch):
    settings = Settings(
        _env_file=None,
        jwt_secret_key=TEST_KEY,
        jwt_access_token_minutes=30,
        jwt_refresh_token_days=7,
    )
    monkeypatch.setattr(tokens, "get_settings", lambda: settings)
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
    user = User(
        id=uuid4(),
        username="alice_01",
        email="alice@example.com",
        password_hash="private-hash-marker",
        role="admin",
        status="active",
        last_login_at=None,
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    authenticate = AsyncMock(return_value=SimpleNamespace(id=user.id, role="user"))
    lookup = AsyncMock(return_value=user)
    store = SimpleNamespace(create=AsyncMock(), revoke=AsyncMock(return_value=True))
    monkeypatch.setattr(login, "authenticate_with_login_limit", authenticate)
    monkeypatch.setattr(
        login,
        "UserRepository",
        Mock(return_value=SimpleNamespace(get_by_id_for_update=lookup)),
    )
    monkeypatch.setattr(login, "SessionStore", Mock(return_value=store))
    context = SimpleNamespace(
        session=session,
        cache=object(),
        user=user,
        authenticate=authenticate,
        lookup=lookup,
        store=store,
        transactions=transactions,
        payload=LoginRequest(identifier="alice_01", password="Sample123"),
    )
    yield context
    asyncio.run(session.close())


def call_login(context):
    return asyncio.run(
        login.login_user(context.payload, context.session, context.cache)
    )


def test_login_persists_refresh_lifetime_and_returns_safe_response_after_commit(
    login_context,
):
    context = login_context
    response = call_login(context)
    access = decode_token(response.access_token, expected_type="access")
    refresh = decode_token(response.refresh_token, expected_type="refresh")
    record = context.store.create.await_args.args[0]

    assert access.sid == refresh.sid == record.id
    assert access.sub == refresh.sub == record.user_id == context.user.id
    assert access.jti != refresh.jti == record.refresh_jti
    assert access.role == refresh.role == "admin"
    assert record.created_at == refresh.iat
    assert record.expires_at == refresh.exp
    assert record.expires_at > access.exp
    assert type(record.created_at) is int
    assert context.transactions == ["commit"]
    assert not context.session.in_transaction()
    assert context.user.last_login_at is not None
    assert response.user.last_login_at.tzinfo is not None
    assert response.expires_in == 1800
    assert response.token_type == "bearer"
    assert "password_hash" not in response.model_dump_json()
    assert response.access_token not in repr(response)
    assert response.refresh_token not in repr(response)
    context.store.revoke.assert_not_awaited()


@pytest.mark.parametrize(
    ("present", "status", "code", "http_status"),
    [
        (False, "active", "AUTH_INVALID_CREDENTIALS", 401),
        (True, "disabled", "USER_DISABLED", 403),
    ],
)
def test_user_is_rechecked_before_issuing_tokens(
    login_context, present, status, code, http_status
):
    context = login_context
    context.user.status = status
    if not present:
        context.lookup.return_value = None

    with pytest.raises(AppException) as caught:
        call_login(context)

    assert caught.value.code == code
    assert caught.value.status_code == http_status
    assert context.transactions == ["rollback"]
    context.store.create.assert_not_awaited()
    context.store.revoke.assert_not_awaited()


@pytest.mark.parametrize(
    "code", ["AUTH_INVALID_CREDENTIALS", "AUTH_LOGIN_RATE_LIMITED"]
)
def test_authentication_rejections_do_not_create_sessions(login_context, code):
    context = login_context
    error = AppException(code=code, message="认证失败", status_code=401)
    context.authenticate.side_effect = error

    with pytest.raises(AppException) as caught:
        call_login(context)

    assert caught.value is error
    assert context.transactions == []
    context.store.create.assert_not_awaited()
    context.store.revoke.assert_not_awaited()


def test_redis_write_failure_rolls_back_and_attempts_session_revocation(login_context):
    context = login_context
    error = RedisError("write outcome unknown")
    context.store.create.side_effect = error

    with pytest.raises(AppException) as caught:
        call_login(context)

    assert caught.value.code == "AUTH_SERVICE_UNAVAILABLE"
    assert caught.value.status_code == 503
    assert caught.value.__cause__ is error
    assert context.transactions == ["rollback"]
    context.store.revoke.assert_awaited_once_with(
        context.store.create.await_args.args[0].id
    )


@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_database_commit_failure_revokes_session_and_preserves_original_error(
    login_context, cleanup_fails, caplog
):
    context = login_context
    error = OperationalError("COMMIT", {}, Exception(2006, "connection lost"))

    def fail_commit(_):
        raise error

    event.listen(context.session.sync_session, "before_commit", fail_commit)
    if cleanup_fails:
        context.store.revoke.side_effect = RedisError("cleanup unavailable")

    with pytest.raises(AppException) as caught:
        call_login(context)

    assert caught.value.code == "AUTH_SERVICE_UNAVAILABLE"
    assert caught.value.status_code == 503
    assert caught.value.__cause__ is error
    assert context.transactions == ["rollback"]
    context.store.revoke.assert_awaited_once_with(
        context.store.create.await_args.args[0].id
    )
    if cleanup_fails:
        assert "login_session_revoke_failed" in caplog.text


def test_database_lookup_failure_does_not_attempt_to_revoke_uncreated_session(
    login_context,
):
    context = login_context
    error = OperationalError("SELECT", {}, Exception(2006, "connection lost"))
    context.lookup.side_effect = error

    with pytest.raises(AppException) as caught:
        call_login(context)

    assert caught.value.code == "AUTH_SERVICE_UNAVAILABLE"
    assert caught.value.__cause__ is error
    assert context.transactions == ["rollback"]
    context.store.create.assert_not_awaited()
    context.store.revoke.assert_not_awaited()
