import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from redis.exceptions import RedisError
from sqlalchemy.exc import OperationalError

from app.core.config import Settings
from app.core.exceptions import AppException
from app.models.user import User
from app.schemas.auth import LoginRequest
from app.services import auth


@pytest.fixture
def limited_authentication_context(monkeypatch):
    settings = Settings(_env_file=None, login_max_failures=3, login_lock_seconds=30)
    failures = SimpleNamespace(
        retry_after=AsyncMock(return_value=0),
        record_failure=AsyncMock(return_value=1),
        clear=AsyncMock(),
    )
    factory = Mock(return_value=failures)
    user = User(
        username="alice_01", email="alice@example.com", password_hash="test-only"
    )
    authenticate = AsyncMock(return_value=user)
    monkeypatch.setattr(auth, "get_settings", lambda: settings)
    monkeypatch.setattr(auth, "LoginFailureStore", factory)
    monkeypatch.setattr(auth, "authenticate_user", authenticate)
    return SimpleNamespace(
        session=object(),
        cache=object(),
        payload=LoginRequest(identifier=" Alice_01 ", password="Sample123"),
        failures=failures,
        factory=factory,
        authenticate=authenticate,
        user=user,
    )


def call_authentication(context):
    return asyncio.run(
        auth.authenticate_with_login_limit(
            context.payload, context.session, context.cache
        )
    )


def test_success_uses_real_settings_and_clears_failures(limited_authentication_context):
    context = limited_authentication_context

    assert call_authentication(context) is context.user
    context.factory.assert_called_once_with(
        context.cache, max_failures=3, lock_seconds=30
    )
    context.failures.retry_after.assert_awaited_once_with("alice_01")
    context.authenticate.assert_awaited_once_with(context.payload, context.session)
    context.failures.clear.assert_awaited_once_with("alice_01")
    context.failures.record_failure.assert_not_awaited()


def test_locked_identifier_is_rejected_before_password_work(
    limited_authentication_context,
):
    context = limited_authentication_context
    context.failures.retry_after.return_value = 12

    with pytest.raises(AppException) as caught:
        call_authentication(context)

    assert caught.value.code == "AUTH_LOGIN_RATE_LIMITED"
    assert caught.value.status_code == 429
    context.authenticate.assert_not_awaited()
    context.failures.record_failure.assert_not_awaited()
    context.failures.clear.assert_not_awaited()


def test_invalid_credentials_increment_count_and_keep_original_error(
    limited_authentication_context,
):
    context = limited_authentication_context
    error = AppException(
        code="AUTH_INVALID_CREDENTIALS", message="账号或密码错误", status_code=401
    )
    context.authenticate.side_effect = error

    with pytest.raises(AppException) as caught:
        call_authentication(context)

    assert caught.value is error
    context.failures.record_failure.assert_awaited_once_with("alice_01")
    context.failures.clear.assert_not_awaited()


@pytest.mark.parametrize("code", ["USER_DISABLED", "OTHER_BUSINESS_FAILURE"])
def test_other_business_errors_do_not_change_failure_count(
    limited_authentication_context, code
):
    context = limited_authentication_context
    error = AppException(code=code, message="账号不可用", status_code=403)
    context.authenticate.side_effect = error

    with pytest.raises(AppException) as caught:
        call_authentication(context)

    assert caught.value is error
    context.failures.record_failure.assert_not_awaited()
    context.failures.clear.assert_not_awaited()


@pytest.mark.parametrize("operation", ["retry_after", "record_failure", "clear"])
def test_any_redis_failure_stops_authentication_with_503(
    limited_authentication_context, operation
):
    context = limited_authentication_context
    error = RedisError("Redis unavailable")
    getattr(context.failures, operation).side_effect = error
    if operation == "record_failure":
        context.authenticate.side_effect = AppException(
            code="AUTH_INVALID_CREDENTIALS", message="账号或密码错误", status_code=401
        )

    with pytest.raises(AppException) as caught:
        call_authentication(context)

    assert caught.value.code == "AUTH_SERVICE_UNAVAILABLE"
    assert caught.value.status_code == 503
    assert caught.value.__cause__ is error
    if operation == "retry_after":
        context.authenticate.assert_not_awaited()


@pytest.mark.parametrize("failure", ["database", "unexpected"])
def test_infrastructure_failures_are_not_counted_as_bad_passwords(
    limited_authentication_context, failure
):
    context = limited_authentication_context
    error = (
        OperationalError("SELECT", {}, Exception(2006, "connection lost"))
        if failure == "database"
        else RuntimeError("unexpected failure")
    )
    context.authenticate.side_effect = error

    with pytest.raises(type(error)) as caught:
        call_authentication(context)

    assert caught.value is error
    context.failures.record_failure.assert_not_awaited()
    context.failures.clear.assert_not_awaited()
