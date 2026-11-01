import asyncio
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy import event
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException
from app.models.user import User
from app.schemas.user import UserResponse


@pytest.fixture
def admin_context(monkeypatch):
    from app.services import admin

    user = User(
        id=uuid4(),
        username="alice",
        email="alice@example.com",
        password_hash="private-hash",
        role="user",
        status="active",
        auth_version=4,
        created_at=datetime(2026, 1, 1),
        updated_at=datetime(2026, 1, 1),
    )
    session = AsyncSession(expire_on_commit=False)
    session.flush = AsyncMock()
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
        get_user=AsyncMock(return_value=user),
        list_users=AsyncMock(return_value=([user], 7)),
    )
    monkeypatch.setattr(admin, "AdminRepository", Mock(return_value=repository))
    yield SimpleNamespace(
        admin=admin,
        user=user,
        session=session,
        repository=repository,
        transactions=transactions,
    )
    asyncio.run(session.close())


def test_list_returns_safe_public_page_with_requested_filters(admin_context):
    context = admin_context
    filters = {"page": 2, "page_size": 3, "q": "alice", "role": "user", "status": None}
    result = asyncio.run(context.admin.list_users(context.session, **filters))
    assert (result.total, result.page, result.page_size) == (7, 2, 3)
    assert result.items == [UserResponse.model_validate(context.user)]
    public = result.model_dump(mode="json")
    assert set(public["items"][0]) == {
        "id",
        "username",
        "email",
        "role",
        "status",
        "last_login_at",
        "created_at",
        "updated_at",
    }
    assert "private-hash" not in result.model_dump_json()
    context.repository.list_users.assert_awaited_once_with(**filters)
    assert context.transactions == ["commit"]
    assert not context.session.in_transaction()


def test_get_returns_existing_user_in_completed_transaction(admin_context):
    context = admin_context
    assert (
        asyncio.run(context.admin.get_user(context.user.id, context.session))
        is context.user
    )
    context.repository.get_user.assert_awaited_once_with(context.user.id)
    context.session.flush.assert_not_awaited()
    assert context.transactions == ["commit"]


@pytest.mark.parametrize("operation", ["get", "set"])
def test_missing_user_raises_safe_404_and_rolls_back(admin_context, operation):
    context = admin_context
    context.repository.get_user.return_value = None
    call = (
        context.admin.get_user(context.user.id, context.session)
        if operation == "get"
        else context.admin.set_user_status(context.user.id, "disabled", context.session)
    )
    with pytest.raises(AppException) as caught:
        asyncio.run(call)
    assert (caught.value.code, caught.value.status_code) == ("USER_NOT_FOUND", 404)
    context.session.flush.assert_not_awaited()
    assert context.transactions == ["rollback"]
    assert not context.session.in_transaction()


@pytest.mark.parametrize("requested", ["active", "disabled"])
def test_admin_account_is_protected_even_for_idempotent_request(
    admin_context, requested
):
    context = admin_context
    context.user.role = "admin"
    with pytest.raises(AppException) as caught:
        asyncio.run(
            context.admin.set_user_status(context.user.id, requested, context.session)
        )
    assert (caught.value.code, caught.value.status_code) == ("ADMIN_PROTECTED", 403)
    assert (context.user.status, context.user.auth_version) == ("active", 4)
    context.session.flush.assert_not_awaited()
    assert context.transactions == ["rollback"]


def test_disable_increments_once_and_reenable_preserves_revocation_version(
    admin_context,
):
    context = admin_context

    async def transitions():
        for status in ("disabled", "disabled", "active", "active"):
            result = await context.admin.set_user_status(
                context.user.id, status, context.session
            )
            assert result is context.user
            assert (result.status, result.auth_version) == (status, 5)

    asyncio.run(transitions())
    assert context.session.flush.await_count == 2
    assert context.repository.get_user.await_count == 4
    context.repository.get_user.assert_awaited_with(context.user.id, for_update=True)
    assert context.transactions == ["commit"] * 4
    assert not context.session.in_transaction()


def test_active_idempotent_request_leaves_version_and_storage_unchanged(admin_context):
    context = admin_context
    result = asyncio.run(
        context.admin.set_user_status(context.user.id, "active", context.session)
    )
    assert (result.status, result.auth_version) == ("active", 4)
    context.session.flush.assert_not_awaited()
    assert context.transactions == ["commit"]


@pytest.mark.parametrize("version", [0, None])
def test_disabling_legacy_account_starts_revocation_version_at_one(
    admin_context, version
):
    context = admin_context
    context.user.auth_version = version
    result = asyncio.run(
        context.admin.set_user_status(context.user.id, "disabled", context.session)
    )
    assert result.auth_version == 1


@pytest.mark.parametrize("operation", ["list", "get", "locked_get", "flush", "commit"])
def test_storage_failures_propagate_and_roll_back(admin_context, operation):
    context = admin_context
    error = OperationalError("UPDATE", {}, Exception(2006, "private-db-marker"))
    if operation == "list":
        context.repository.list_users.side_effect = error
        call = context.admin.list_users(
            context.session, page=1, page_size=20, q=None, role=None, status=None
        )
    elif operation == "get":
        context.repository.get_user.side_effect = error
        call = context.admin.get_user(context.user.id, context.session)
    else:
        if operation == "locked_get":
            context.repository.get_user.side_effect = error
        elif operation == "flush":
            context.session.flush.side_effect = error
        else:

            def fail_commit(_):
                raise error

            event.listen(context.session.sync_session, "before_commit", fail_commit)
        call = context.admin.set_user_status(
            context.user.id, "disabled", context.session
        )
    with pytest.raises(OperationalError) as caught:
        asyncio.run(call)
    assert caught.value is error
    assert context.transactions == ["rollback"]
    assert not context.session.in_transaction()
