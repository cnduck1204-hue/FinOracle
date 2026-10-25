import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException
from app.models.user import User
from app.schemas.auth import LoginRequest
from app.security.passwords import DUMMY_PASSWORD_HASH, verify_password
from app.services import auth


@pytest.fixture
def authentication_context(monkeypatch):
    session = AsyncSession(expire_on_commit=False)
    user = User(
        username="alice_01",
        email="alice@example.com",
        password_hash="stored-password-hash",
        role="user",
        status="active",
    )
    lookup = AsyncMock(return_value=user)
    factory = Mock(return_value=SimpleNamespace(get_by_identifier=lookup))

    async def check_password(*args):
        assert not session.in_transaction(), "Password work must not hold a transaction"
        return True

    verifier = AsyncMock(side_effect=check_password)
    monkeypatch.setattr(auth, "UserRepository", factory)
    monkeypatch.setattr(auth, "verify_password", verifier)
    yield SimpleNamespace(
        session=session,
        user=user,
        lookup=lookup,
        verifier=verifier,
        factory=factory,
    )
    asyncio.run(session.close())


@pytest.mark.parametrize(
    ("supplied", "normalized"),
    [(" Alice_01 ", "alice_01"), (" Alice@EXAMPLE.COM ", "alice@example.com")],
)
def test_valid_credentials_return_user_without_changing_login_time(
    authentication_context, supplied, normalized
):
    context = authentication_context
    password = " FinOracle密码123 "
    payload = LoginRequest(identifier=supplied, password=password)

    user = asyncio.run(auth.authenticate_user(payload, context.session))

    assert user is context.user
    context.factory.assert_called_once_with(context.session)
    context.lookup.assert_awaited_once_with(normalized)
    context.verifier.assert_awaited_once_with(password, user.password_hash)
    assert user.last_login_at is None
    assert not context.session.in_transaction()


@pytest.mark.parametrize(
    ("exists", "matches"), [(False, False), (False, True), (True, False)]
)
def test_missing_user_and_wrong_password_have_identical_errors(
    authentication_context, exists, matches
):
    context = authentication_context
    if not exists:
        context.lookup.return_value = None
    context.verifier.side_effect = None
    context.verifier.return_value = matches
    payload = LoginRequest(identifier="alice_01", password="Wrong123")

    with pytest.raises(AppException) as caught:
        asyncio.run(auth.authenticate_user(payload, context.session))

    assert (caught.value.code, caught.value.message, caught.value.status_code) == (
        "AUTH_INVALID_CREDENTIALS",
        "账号或密码错误",
        401,
    )
    expected_hash = context.user.password_hash if exists else DUMMY_PASSWORD_HASH
    context.verifier.assert_awaited_once_with("Wrong123", expected_hash)
    assert not context.session.in_transaction()


@pytest.mark.parametrize("password_matches", [False, True])
def test_disabled_status_is_revealed_only_after_valid_password(
    authentication_context, password_matches
):
    context = authentication_context
    context.user.status = "disabled"
    context.verifier.side_effect = None
    context.verifier.return_value = password_matches
    payload = LoginRequest(identifier="alice_01", password="Sample123")

    with pytest.raises(AppException) as caught:
        asyncio.run(auth.authenticate_user(payload, context.session))

    assert caught.value.code == (
        "USER_DISABLED" if password_matches else "AUTH_INVALID_CREDENTIALS"
    )
    assert caught.value.status_code == (403 if password_matches else 401)
    context.verifier.assert_awaited_once()


def test_database_errors_are_not_reported_as_invalid_credentials(
    authentication_context,
):
    context = authentication_context
    error = OperationalError("SELECT", {}, Exception(2006, "connection lost"))
    context.lookup.side_effect = error

    with pytest.raises(OperationalError) as caught:
        asyncio.run(
            auth.authenticate_user(
                LoginRequest(identifier="alice_01", password="Sample123"),
                context.session,
            )
        )

    assert caught.value is error
    context.verifier.assert_not_awaited()
    assert not context.session.in_transaction()


def test_unexpected_password_verification_failure_propagates(authentication_context):
    context = authentication_context
    error = RuntimeError("verification failed")
    context.verifier.side_effect = error

    with pytest.raises(RuntimeError) as caught:
        asyncio.run(
            auth.authenticate_user(
                LoginRequest(identifier="alice_01", password="Sample123"),
                context.session,
            )
        )

    assert caught.value is error
    assert not context.session.in_transaction()


def test_dummy_hash_runs_real_argon2_verification_instead_of_rejecting_its_format(
    monkeypatch,
):
    from argon2 import PasswordHasher
    from argon2.exceptions import VerifyMismatchError

    from app.security import passwords

    real_hasher = PasswordHasher()
    mismatch_seen = []

    def verify(encoded, supplied):
        try:
            return real_hasher.verify(encoded, supplied)
        except VerifyMismatchError:
            mismatch_seen.append(True)
            raise

    monkeypatch.setattr(passwords, "_password_hasher", SimpleNamespace(verify=verify))

    assert (
        asyncio.run(verify_password("dummy-check-only", DUMMY_PASSWORD_HASH)) is False
    )
    assert mismatch_seen == [True]
