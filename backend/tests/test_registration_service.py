import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy import event
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException
from app.schemas.auth import RegisterRequest
from app.services import auth


@pytest.fixture
def registration_context(monkeypatch):
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
    repository = SimpleNamespace(
        get_by_username=AsyncMock(return_value=None),
        get_by_email=AsyncMock(return_value=None),
        add=AsyncMock(),
    )
    factory = Mock(return_value=repository)
    hasher = AsyncMock(return_value="encoded-password-hash")
    monkeypatch.setattr(auth, "UserRepository", factory)
    monkeypatch.setattr(auth, "hash_password", hasher)
    password = " FinOracle密码123 "
    payload = RegisterRequest(
        username=" Alice_01 ",
        email="Alice@EXAMPLE.COM",
        password=password,
        confirm_password=password,
    )
    yield SimpleNamespace(
        session=session,
        transactions=transactions,
        repository=repository,
        factory=factory,
        hasher=hasher,
        payload=payload,
        password=password,
    )
    asyncio.run(session.close())


def test_registration_hashes_exact_password_and_commits_regular_user(
    registration_context,
):
    context = registration_context
    user = asyncio.run(auth.register_user(context.payload, context.session))

    context.hasher.assert_awaited_once_with(context.password)
    context.factory.assert_called_once_with(context.session)
    context.repository.get_by_username.assert_awaited_once_with("alice_01")
    context.repository.get_by_email.assert_awaited_once_with("alice@example.com")
    context.repository.add.assert_awaited_once_with(user)
    assert user.username == "alice_01"
    assert user.email == "alice@example.com"
    assert user.password_hash == "encoded-password-hash"
    assert user.password_hash != context.password
    assert user.role == "user"
    assert user.status == "active"
    assert context.transactions == ["commit"]
    assert not context.session.in_transaction()


@pytest.mark.parametrize(
    ("lookup", "code"),
    [
        ("get_by_username", "USER_USERNAME_EXISTS"),
        ("get_by_email", "USER_EMAIL_EXISTS"),
    ],
)
def test_existing_identifier_prevents_insert_and_rolls_back(
    registration_context, lookup, code
):
    context = registration_context
    getattr(context.repository, lookup).return_value = object()

    with pytest.raises(AppException) as caught:
        asyncio.run(auth.register_user(context.payload, context.session))

    assert caught.value.code == code
    assert caught.value.status_code == 409
    context.repository.add.assert_not_awaited()
    assert context.transactions == ["rollback"]
    assert not context.session.in_transaction()


@pytest.mark.parametrize("error_args", [(1062, "duplicate key"), (1452, "bad key"), ()])
def test_only_duplicate_key_errors_become_conflicts(registration_context, error_args):
    context = registration_context
    error = IntegrityError("INSERT", {}, Exception(*error_args))
    context.repository.add.side_effect = error

    expected_error = (
        AppException if error_args and error_args[0] == 1062 else IntegrityError
    )
    with pytest.raises(expected_error) as caught:
        asyncio.run(auth.register_user(context.payload, context.session))

    if expected_error is AppException:
        assert caught.value.code == "USER_ALREADY_EXISTS"
        assert caught.value.status_code == 409
        assert caught.value.__cause__ is error
    else:
        assert caught.value is error
    assert context.transactions == ["rollback"]
    assert not context.session.in_transaction()


def test_database_outage_is_not_reported_as_duplicate_user(registration_context):
    context = registration_context
    error = OperationalError("INSERT", {}, Exception(2006, "connection lost"))
    context.repository.add.side_effect = error

    with pytest.raises(OperationalError) as caught:
        asyncio.run(auth.register_user(context.payload, context.session))

    assert caught.value is error
    assert context.transactions == ["rollback"]
    assert not context.session.in_transaction()


def test_hash_failure_does_not_begin_database_transaction(registration_context):
    context = registration_context
    error = RuntimeError("hashing failed")
    context.hasher.side_effect = error

    with pytest.raises(RuntimeError) as caught:
        asyncio.run(auth.register_user(context.payload, context.session))

    assert caught.value is error
    context.repository.get_by_username.assert_not_awaited()
    context.repository.add.assert_not_awaited()
    assert context.transactions == []
    assert not context.session.in_transaction()


def test_commit_failure_is_propagated_and_transaction_is_rolled_back(
    registration_context,
):
    context = registration_context
    error = RuntimeError("commit failed")

    def fail_commit(_):
        raise error

    event.listen(context.session.sync_session, "before_commit", fail_commit)

    with pytest.raises(RuntimeError) as caught:
        asyncio.run(auth.register_user(context.payload, context.session))

    assert caught.value is error
    assert context.transactions == ["rollback"]
    assert not context.session.in_transaction()
