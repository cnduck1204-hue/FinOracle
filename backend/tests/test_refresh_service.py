import asyncio
from time import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import jwt
import pytest
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
from app.services import refresh

TEST_KEY = "d98f15c0e2b64aa7b91d0438f5c6e270a18b3f9d42e65c07b6a1d8e34f9205ac"


@pytest.fixture
def refresh_context(monkeypatch):
    settings = Settings(_env_file=None, jwt_secret_key=TEST_KEY)
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
        password_hash="private-refresh-hash-marker",
        role="admin",
        status="active",
        last_login_at=utc_now(),
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    token = create_token(
        user_id=user.id,
        role="user",
        session_id=uuid4(),
        token_id=uuid4(),
        token_type="refresh",
    )
    claims = decode_token(token, expected_type="refresh")
    record = SessionRecord(
        id=claims.sid,
        user_id=claims.sub,
        refresh_jti=claims.jti,
        created_at=claims.iat,
        expires_at=claims.exp,
    )
    lookup = AsyncMock(return_value=user)
    monkeypatch.setattr(
        refresh, "UserRepository", Mock(return_value=SimpleNamespace(get_by_id=lookup))
    )

    async def rotate(**kwargs):
        assert not session.in_transaction(), (
            "Redis rotation must follow the completed DB transaction"
        )
        assert transactions == ["commit"]
        return "rotated"

    store = SimpleNamespace(
        get=AsyncMock(return_value=record),
        rotate=AsyncMock(side_effect=rotate),
        revoke=AsyncMock(return_value=True),
    )
    monkeypatch.setattr(refresh, "SessionStore", Mock(return_value=store))
    yield SimpleNamespace(
        session=session,
        cache=object(),
        user=user,
        token=token,
        claims=claims,
        store=store,
        lookup=lookup,
        record=record,
        transactions=transactions,
    )
    asyncio.run(session.close())


def call_refresh(context, token=None):
    return asyncio.run(
        refresh.refresh_tokens(
            context.token if token is None else token,
            context.session,
            context.cache,
        )
    )


def test_refresh_returns_new_pair_after_db_commit_and_session_rotation(refresh_context):
    context = refresh_context
    times = (
        context.user.last_login_at,
        context.user.created_at,
        context.user.updated_at,
    )

    response = call_refresh(context)

    access = decode_token(response.access_token, expected_type="access")
    renewed = decode_token(response.refresh_token, expected_type="refresh")
    assert access.sid == renewed.sid == context.claims.sid
    assert access.sub == renewed.sub == context.user.id
    assert len({access.jti, renewed.jti, context.claims.jti}) == 3
    assert access.role == renewed.role == response.user.role == "admin"
    assert context.claims.role == "user"
    context.store.rotate.assert_awaited_once_with(
        session_id=context.claims.sid,
        user_id=context.claims.sub,
        expected_refresh_jti=context.claims.jti,
        new_refresh_jti=renewed.jti,
        expires_at=renewed.exp,
    )
    context.lookup.assert_awaited_once_with(context.claims.sub)
    assert response.expires_in == access.exp - access.iat == 1800
    assert renewed.exp - renewed.iat == 7 * 24 * 3600
    assert response.user.last_login_at.tzinfo is not None
    assert times == (
        context.user.last_login_at,
        context.user.created_at,
        context.user.updated_at,
    )
    for secret in [context.user.password_hash, context.token]:
        assert secret not in response.model_dump_json()
    assert response.access_token not in repr(response)
    assert response.refresh_token not in repr(response)
    context.store.revoke.assert_not_awaited()


@pytest.mark.parametrize("mode", ["malformed", "access", "expired"])
def test_invalid_refresh_tokens_are_rejected_before_storage(refresh_context, mode):
    context = refresh_context
    token = "malformed-token-marker"
    if mode == "access":
        token = create_token(
            user_id=context.user.id,
            role="user",
            session_id=context.claims.sid,
            token_id=uuid4(),
            token_type="access",
        )
    elif mode == "expired":
        payload = context.claims.model_dump(mode="json")
        payload.update(iat=int(time()) - 120, exp=int(time()) - 60)
        token = jwt.encode(payload, TEST_KEY, algorithm="HS256")
    with pytest.raises(AppException) as caught:
        call_refresh(context, token)
    assert (caught.value.code, caught.value.status_code) == ("AUTH_INVALID_TOKEN", 401)
    context.store.get.assert_not_awaited()
    context.lookup.assert_not_awaited()
    context.store.rotate.assert_not_awaited()
    context.store.revoke.assert_not_awaited()


@pytest.mark.parametrize("mode", ["missing", "revoked", "other-user"])
def test_invalid_session_is_rejected_without_rotation_or_revocation(
    refresh_context, mode
):
    context = refresh_context
    if mode == "missing":
        context.store.get.return_value = None
    else:
        changes = {"revoked": True} if mode == "revoked" else {"user_id": uuid4()}
        context.store.get.return_value = context.record.model_copy(update=changes)
    with pytest.raises(AppException) as caught:
        call_refresh(context)
    assert (caught.value.code, caught.value.status_code) == ("AUTH_INVALID_TOKEN", 401)
    context.lookup.assert_not_awaited()
    context.store.rotate.assert_not_awaited()
    context.store.revoke.assert_not_awaited()


@pytest.mark.parametrize("mode", ["deleted", "disabled"])
def test_latest_user_state_is_checked_before_refresh(refresh_context, mode):
    context = refresh_context
    if mode == "deleted":
        context.lookup.return_value = None
    else:
        context.user.status = "disabled"
    with pytest.raises(AppException) as caught:
        call_refresh(context)
    expected = (
        ("AUTH_INVALID_TOKEN", 401) if mode == "deleted" else ("USER_DISABLED", 403)
    )
    assert (caught.value.code, caught.value.status_code) == expected
    assert context.transactions == ["rollback"]
    context.store.rotate.assert_not_awaited()
    context.store.revoke.assert_not_awaited()


@pytest.mark.parametrize("outcome", ["invalid", "replayed"])
def test_atomic_rotation_decides_replay_even_when_jti_changed_since_read(
    refresh_context, outcome
):
    context = refresh_context
    context.store.get.return_value = context.record.model_copy(
        update={"refresh_jti": uuid4()}
    )
    context.store.rotate.side_effect = None
    context.store.rotate.return_value = outcome
    with pytest.raises(AppException) as caught:
        call_refresh(context)
    assert (caught.value.code, caught.value.status_code) == ("AUTH_INVALID_TOKEN", 401)
    context.store.rotate.assert_awaited_once()
    assert (
        context.store.rotate.await_args.kwargs["expected_refresh_jti"]
        == context.claims.jti
    )
    context.store.revoke.assert_not_awaited()


@pytest.mark.parametrize("error_type", [RedisError, ValueError])
def test_session_read_failure_is_503_without_revoking_existing_session(
    refresh_context, error_type
):
    context = refresh_context
    error = error_type("private-storage-error-marker")
    context.store.get.side_effect = error
    with pytest.raises(AppException) as caught:
        call_refresh(context)
    assert (caught.value.code, caught.value.status_code) == (
        "AUTH_SERVICE_UNAVAILABLE",
        503,
    )
    assert caught.value.__cause__ is error
    context.store.rotate.assert_not_awaited()
    context.store.revoke.assert_not_awaited()


@pytest.mark.parametrize("phase", ["lookup", "commit"])
def test_database_failures_leave_old_refresh_session_untouched(refresh_context, phase):
    context = refresh_context
    error = OperationalError("SELECT", {}, Exception(2006, "connection lost"))
    if phase == "lookup":
        context.lookup.side_effect = error
    else:

        def fail_commit(_):
            raise error

        event.listen(context.session.sync_session, "before_commit", fail_commit)
    with pytest.raises(AppException) as caught:
        call_refresh(context)
    assert (caught.value.code, caught.value.status_code) == (
        "AUTH_SERVICE_UNAVAILABLE",
        503,
    )
    assert caught.value.__cause__ is error
    assert context.transactions == ["rollback"]
    assert not context.session.in_transaction()
    context.store.rotate.assert_not_awaited()
    context.store.revoke.assert_not_awaited()


@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_uncertain_rotation_outcome_revokes_session_and_preserves_original_error(
    refresh_context, cleanup_fails, caplog
):
    context = refresh_context
    error = RedisError("rotation reply lost")
    context.store.rotate.side_effect = error
    if cleanup_fails:
        context.store.revoke.side_effect = RedisError("cleanup unavailable")
    with pytest.raises(AppException) as caught:
        call_refresh(context)
    assert (caught.value.code, caught.value.status_code) == (
        "AUTH_SERVICE_UNAVAILABLE",
        503,
    )
    assert caught.value.__cause__ is error
    context.store.revoke.assert_awaited_once_with(context.claims.sid)
    assert not context.session.in_transaction()
    if cleanup_fails:
        assert "refresh_session_revoke_failed" in caplog.text
    assert context.token not in caplog.text
    assert context.user.password_hash not in caplog.text


def test_unexpected_rotation_result_error_also_revokes_session(refresh_context):
    context = refresh_context
    context.store.rotate.side_effect = ValueError("unknown script result")
    with pytest.raises(AppException) as caught:
        call_refresh(context)
    assert (caught.value.code, caught.value.status_code) == (
        "AUTH_SERVICE_UNAVAILABLE",
        503,
    )
    context.store.revoke.assert_awaited_once_with(context.claims.sid)


def test_response_validation_happens_before_consuming_refresh_token(refresh_context):
    context = refresh_context
    context.user.email = "invalid-email"
    with pytest.raises(AppException) as caught:
        call_refresh(context)
    assert (caught.value.code, caught.value.status_code) == (
        "AUTH_SERVICE_UNAVAILABLE",
        503,
    )
    context.store.rotate.assert_not_awaited()
    context.store.revoke.assert_not_awaited()
    assert context.transactions == ["rollback"]


def test_unexpected_programming_error_propagates_without_revocation(refresh_context):
    context = refresh_context
    error = RuntimeError("unexpected lookup error")
    context.lookup.side_effect = error
    with pytest.raises(RuntimeError) as caught:
        call_refresh(context)
    assert caught.value is error
    assert context.transactions == ["rollback"]
    context.store.rotate.assert_not_awaited()
    context.store.revoke.assert_not_awaited()
