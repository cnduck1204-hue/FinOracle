import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError
from redis.exceptions import RedisError

from app.cache.login_failures import LoginFailureStore
from app.core.config import Settings


@pytest.mark.parametrize(("limit", "seconds"), [(0, 900), (-1, 900), (5, 0), (5, -1)])
def test_failure_store_rejects_nonpositive_configuration(limit, seconds):
    with pytest.raises(ValueError):
        LoginFailureStore(object(), max_failures=limit, lock_seconds=seconds)


@pytest.mark.parametrize("count", [1, 5])
def test_record_failure_returns_updated_count_after_awaiting_redis(count):
    cache = SimpleNamespace(eval=AsyncMock(return_value=count))
    store = LoginFailureStore(cache, max_failures=5, lock_seconds=900)

    result = asyncio.run(store.record_failure("alice@example.com"))

    cache.eval.assert_awaited_once()
    assert result == count, "record_failure must return the updated failure count"


@pytest.mark.parametrize("seconds", [0, 12])
def test_retry_after_returns_remaining_seconds(seconds):
    cache = SimpleNamespace(eval=AsyncMock(return_value=seconds))
    store = LoginFailureStore(cache, max_failures=5, lock_seconds=900)

    assert asyncio.run(store.retry_after("alice@example.com")) == seconds
    cache.eval.assert_awaited_once()


def test_clear_waits_for_redis_deletion():
    cache = SimpleNamespace(delete=AsyncMock(return_value=1))
    store = LoginFailureStore(cache, max_failures=5, lock_seconds=900)

    assert asyncio.run(store.clear("alice@example.com")) is None
    cache.delete.assert_awaited_once()


def test_identifiers_use_stable_distinct_keys_without_plaintext_email():
    cache = SimpleNamespace(eval=AsyncMock(return_value=0), delete=AsyncMock())
    store = LoginFailureStore(cache, max_failures=5, lock_seconds=900)
    identifier = "alice@example.com"

    async def check():
        await store.retry_after(identifier)
        await store.record_failure(identifier)
        await store.clear(identifier)
        await store.retry_after("bob@example.com")

    asyncio.run(check())
    first_key, failure_key, other_key = [
        call.args[2] for call in cache.eval.await_args_list
    ]
    assert first_key == failure_key == cache.delete.await_args.args[0]
    assert first_key != other_key
    assert first_key.startswith("finoracle:v1:auth:login-failures:")
    assert identifier not in first_key
    assert "@" not in first_key


@pytest.mark.parametrize("method", ["retry_after", "record_failure", "clear"])
def test_redis_errors_are_propagated_instead_of_ignoring_limits(method):
    error = RedisError("Redis unavailable")
    cache = SimpleNamespace(
        eval=AsyncMock(side_effect=error), delete=AsyncMock(side_effect=error)
    )
    store = LoginFailureStore(cache, max_failures=5, lock_seconds=900)

    with pytest.raises(RedisError) as caught:
        asyncio.run(getattr(store, method)("alice@example.com"))

    assert caught.value is error


def test_login_limit_settings_have_expected_defaults(monkeypatch):
    monkeypatch.delenv("LOGIN_MAX_FAILURES", raising=False)
    monkeypatch.delenv("LOGIN_LOCK_SECONDS", raising=False)
    settings = Settings(_env_file=None)

    assert settings.login_max_failures == 5
    assert settings.login_lock_seconds == 900


def test_login_limit_settings_can_be_overridden_by_environment(monkeypatch):
    monkeypatch.setenv("LOGIN_MAX_FAILURES", "3")
    monkeypatch.setenv("LOGIN_LOCK_SECONDS", "60")
    settings = Settings(_env_file=None)

    assert settings.login_max_failures == 3
    assert settings.login_lock_seconds == 60


@pytest.mark.parametrize("field", ["LOGIN_MAX_FAILURES", "LOGIN_LOCK_SECONDS"])
def test_login_limit_settings_reject_zero(field, monkeypatch):
    monkeypatch.setenv(field, "0")

    with pytest.raises(ValidationError):
        Settings(_env_file=None)
