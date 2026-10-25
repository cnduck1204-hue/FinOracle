import asyncio
from unittest.mock import AsyncMock

import pytest
from redis.exceptions import RedisError

from app.core.exceptions import AppException
from app.services.logout import logout_user
from tests.test_current_user import current_context as current_context


@pytest.fixture(autouse=True)
def async_redis_eval(current_context):
    current_context.cache.eval = AsyncMock(return_value=1)


@pytest.mark.parametrize("all_sessions", [False, True])
def test_logout_authenticates_then_revokes_without_changing_user(
    current_context, all_sessions
):
    context = current_context
    context.cache.eval.return_value = 1
    original_login = context.user.last_login_at
    assert (
        asyncio.run(
            logout_user(
                context.token, context.session, context.cache, all_sessions=all_sessions
            )
        )
        is None
    )
    assert context.user.last_login_at == original_login
    assert context.transactions == ["commit"]
    assert not context.session.in_transaction()
    context.cache.eval.assert_awaited_once()


@pytest.mark.parametrize("all_sessions", [False, True])
def test_logout_storage_failure_never_becomes_success(current_context, all_sessions):
    context = current_context
    error = RedisError("injected unavailable cache")
    context.cache.eval.side_effect = error
    with pytest.raises(AppException) as caught:
        asyncio.run(
            logout_user(
                context.token, context.session, context.cache, all_sessions=all_sessions
            )
        )
    assert (caught.value.code, caught.value.status_code) == (
        "AUTH_SERVICE_UNAVAILABLE",
        503,
    )
    assert caught.value.__cause__ is error


@pytest.mark.parametrize("mode", ["disabled", "revoked", "wrong-token"])
def test_logout_cannot_skip_authentication(current_context, mode):
    context = current_context
    token = context.token
    if mode == "disabled":
        context.user.status = "disabled"
    elif mode == "revoked":
        context.cache.get.return_value = context.record.model_copy(
            update={"revoked": True}
        ).model_dump_json()
    else:
        token = "bad-token"
    with pytest.raises(AppException) as caught:
        asyncio.run(
            logout_user(token, context.session, context.cache, all_sessions=True)
        )
    assert caught.value.status_code == (403 if mode == "disabled" else 401)
    context.cache.eval.assert_not_awaited()
