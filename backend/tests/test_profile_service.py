import asyncio
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy import event
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException
from app.models.user import User


@pytest.fixture
def profile_context(monkeypatch):
    from app.services import profile

    user = User(
        id=uuid4(),
        username="alice",
        email="alice@example.com",
        password_hash="private-hash",
        role="user",
        status="active",
        created_at=datetime(2026, 1, 1),
        updated_at=datetime(2026, 1, 1),
    )
    session = AsyncSession(expire_on_commit=False)
    session.flush = AsyncMock()
    session.refresh = AsyncMock()
    transactions = []
    event.listen(
        session.sync_session, "after_commit", lambda _: transactions.append("commit")
    )
    event.listen(
        session.sync_session,
        "after_rollback",
        lambda _: transactions.append("rollback"),
    )
    repository = SimpleNamespace(
        get_by_id_for_update=AsyncMock(return_value=user),
        get_by_username=AsyncMock(return_value=None),
        get_by_email=AsyncMock(return_value=None),
    )
    monkeypatch.setattr(profile, "UserRepository", Mock(return_value=repository))
    yield SimpleNamespace(
        profile=profile,
        user=user,
        session=session,
        repository=repository,
        transactions=transactions,
    )
    asyncio.run(session.close())


def update(context, data):
    from app.schemas.profile import ProfileUpdateRequest

    return asyncio.run(
        context.profile.update_profile(
            ProfileUpdateRequest.model_validate(data),
            context.user.id,
            context.session,
        )
    )


@pytest.mark.parametrize("data", [{"username": " Bob "}, {"email": "BOB@example.com"}])
def test_partial_profile_update_uses_locked_current_user(profile_context, data):
    context = profile_context
    before = (context.user.username, context.user.email)
    result = update(context, data)
    assert result is context.user
    assert result.username == ("bob" if "username" in data else before[0])
    assert result.email == ("bob@example.com" if "email" in data else before[1])
    assert (result.role, result.status, result.password_hash) == (
        "user",
        "active",
        "private-hash",
    )
    context.repository.get_by_id_for_update.assert_awaited_once_with(result.id)
    context.session.flush.assert_awaited_once()
    context.session.refresh.assert_awaited_once_with(result)
    assert context.transactions == ["commit"]
    assert not context.session.in_transaction()


def test_profile_returns_refreshed_database_timestamps(profile_context):
    context = profile_context
    latest = datetime(2026, 10, 28)

    async def refresh(user):
        user.updated_at = latest

    context.session.refresh.side_effect = refresh
    assert update(context, {"username": "bob"}).updated_at == latest


def test_same_identifiers_are_allowed(profile_context):
    context = profile_context
    context.repository.get_by_username.return_value = context.user
    context.repository.get_by_email.return_value = context.user
    assert (
        update(context, {"username": "ALICE", "email": "ALICE@example.com"})
        is context.user
    )
    assert context.transactions == ["commit"]


@pytest.mark.parametrize(
    ("field", "code"),
    [("username", "USER_USERNAME_EXISTS"), ("email", "USER_EMAIL_EXISTS")],
)
def test_other_users_identifiers_conflict(profile_context, field, code):
    context = profile_context
    getattr(context.repository, f"get_by_{field}").return_value = SimpleNamespace(
        id=uuid4()
    )
    with pytest.raises(AppException) as caught:
        update(context, {field: "bob" if field == "username" else "bob@example.com"})
    assert (caught.value.code, caught.value.status_code) == (code, 409)
    assert (context.user.username, context.user.email) == ("alice", "alice@example.com")
    context.session.flush.assert_not_awaited()
    assert context.transactions == ["rollback"]


@pytest.mark.parametrize(
    ("state", "code", "status"),
    [("deleted", "AUTH_INVALID_TOKEN", 401), ("disabled", "USER_DISABLED", 403)],
)
def test_profile_rechecks_account_under_lock(profile_context, state, code, status):
    context = profile_context
    if state == "deleted":
        context.repository.get_by_id_for_update.return_value = None
    else:
        context.user.status = state
    with pytest.raises(AppException) as caught:
        update(context, {"username": "bob"})
    assert (caught.value.code, caught.value.status_code) == (code, status)
    context.session.flush.assert_not_awaited()
    assert context.transactions == ["rollback"]


@pytest.mark.parametrize(
    "error_args", [(1062, "private-key-marker"), (1452, "private-key-marker"), ()]
)
def test_profile_only_maps_duplicate_unique_errors(profile_context, error_args):
    context = profile_context
    error = IntegrityError("UPDATE", {}, Exception(*error_args))
    context.session.flush.side_effect = error
    expected = AppException if error_args and error_args[0] == 1062 else IntegrityError
    with pytest.raises(expected) as caught:
        update(context, {"username": "bob"})
    if expected is AppException:
        assert (caught.value.code, caught.value.status_code) == (
            "USER_ALREADY_EXISTS",
            409,
        )
        assert caught.value.__cause__ is error
        assert "private-key-marker" not in str(caught.value)
    else:
        assert caught.value is error
    assert context.transactions == ["rollback"]
    assert not context.session.in_transaction()


@pytest.mark.parametrize("operation", ["lookup", "flush", "refresh", "commit"])
def test_profile_storage_failures_propagate(profile_context, operation):
    context = profile_context
    error = OperationalError("UPDATE", {}, Exception(2006, "private-db-marker"))
    if operation == "lookup":
        context.repository.get_by_id_for_update.side_effect = error
    elif operation == "commit":

        def fail_commit(_):
            raise error

        event.listen(context.session.sync_session, "before_commit", fail_commit)
    else:
        getattr(context.session, operation).side_effect = error
    with pytest.raises(OperationalError) as caught:
        update(context, {"username": "bob"})
    assert caught.value is error
    assert context.transactions == ["rollback"]
    assert not context.session.in_transaction()
