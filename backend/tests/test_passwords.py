import asyncio
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from argon2.exceptions import HashingError

from app.security import passwords


@pytest.fixture(scope="module")
def password_samples():
    plain = " FinOracle密码123 "

    async def create():
        return (
            plain,
            await passwords.hash_password(plain),
            await passwords.hash_password(plain),
        )

    return asyncio.run(create())


def test_hash_uses_argon2id_and_fits_database_column(password_samples):
    plain, encoded, _ = password_samples
    assert encoded.startswith("$argon2id$")
    assert encoded != plain
    assert len(encoded) <= 255


def test_same_password_gets_independent_salts(password_samples):
    plain, first, second = password_samples
    assert first != second

    async def check():
        assert await passwords.verify_password(plain, first)
        assert await passwords.verify_password(plain, second)

    asyncio.run(check())


def test_correct_password_is_accepted(password_samples):
    plain, encoded, _ = password_samples
    assert asyncio.run(passwords.verify_password(plain, encoded)) is True


@pytest.mark.parametrize("variant", ["different", "trimmed", "case_changed"])
def test_wrong_or_normalized_password_is_rejected(password_samples, variant):
    plain, encoded, _ = password_samples
    supplied = {
        "different": "AnotherPassword123",
        "trimmed": plain.strip(),
        "case_changed": plain.swapcase(),
    }[variant]
    assert asyncio.run(passwords.verify_password(supplied, encoded)) is False


@pytest.mark.parametrize("encoded", ["", "invalid-hash", "$argon2id$invalid-encoding"])
def test_invalid_stored_hash_is_rejected(encoded):
    assert asyncio.run(passwords.verify_password("SamplePassword123", encoded)) is False


@pytest.mark.parametrize("operation", ["hash", "verify"])
def test_password_computation_does_not_block_event_loop(monkeypatch, operation):
    started = Event()
    release = Event()
    expected = "encoded-test-value" if operation == "hash" else True

    def blocking_work(*args):
        started.set()
        if not release.wait(timeout=2):
            raise RuntimeError("Password computation blocked the event loop")
        return expected

    monkeypatch.setattr(
        passwords,
        "_password_hasher",
        SimpleNamespace(hash=blocking_work, verify=blocking_work),
    )

    async def wait_until_started():
        while not started.is_set():
            await asyncio.sleep(0.001)

    async def check():
        coroutine = (
            passwords.hash_password("sample")
            if operation == "hash"
            else passwords.verify_password("sample", "encoded")
        )
        task = asyncio.create_task(coroutine)
        try:
            await asyncio.wait_for(wait_until_started(), timeout=1)
            assert not task.done(), "Password work completed before the loop could run"
        finally:
            release.set()
            result = await task
        assert result == expected

    asyncio.run(check())


def test_hashing_failure_is_not_silently_accepted(monkeypatch):
    error = HashingError("Hash computation failed")
    monkeypatch.setattr(
        passwords, "_password_hasher", SimpleNamespace(hash=Mock(side_effect=error))
    )
    with pytest.raises(HashingError) as caught:
        asyncio.run(passwords.hash_password("SamplePassword123"))
    assert caught.value is error


def test_unexpected_verification_error_is_not_hidden(monkeypatch):
    error = RuntimeError("Unexpected verifier failure")
    monkeypatch.setattr(
        passwords, "_password_hasher", SimpleNamespace(verify=Mock(side_effect=error))
    )
    with pytest.raises(RuntimeError) as caught:
        asyncio.run(passwords.verify_password("SamplePassword123", "encoded"))
    assert caught.value is error
