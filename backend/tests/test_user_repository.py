import asyncio
import inspect
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.repositories.user import UserRepository


@pytest.mark.parametrize(
    ("method", "identifier"),
    [("get_by_username", "alice_01"), ("get_by_email", "alice@example.com")],
)
@pytest.mark.parametrize("exists", [False, True], ids=["missing-user", "existing-user"])
def test_lookup_returns_resolved_user_or_none(method, identifier, exists):
    session = Mock(spec=AsyncSession)
    expected = (
        User(
            username="alice_01",
            email="alice@example.com",
            password_hash="test-hash",
        )
        if exists
        else None
    )
    session.scalar.return_value = expected
    repository = UserRepository(session)

    result = asyncio.run(getattr(repository, method)(identifier))
    try:
        assert result is expected, (
            "Lookup must return a user or None, never a coroutine"
        )
        session.scalar.assert_awaited_once()
    finally:
        # Close an incorrectly returned coroutine so a failure does not leak it.
        if inspect.iscoroutine(result):
            result.close()


def test_add_waits_for_insert_and_does_not_commit_transaction():
    session = Mock(spec=AsyncSession)
    repository = UserRepository(session)
    user = User(
        username="alice_01",
        email="alice@example.com",
        password_hash="test-hash",
    )

    result = asyncio.run(repository.add(user))
    try:
        session.add.assert_called_once_with(user)
        session.flush.assert_awaited_once()
        assert result is None
        session.commit.assert_not_called()
    finally:
        if inspect.iscoroutine(result):
            result.close()


@pytest.mark.parametrize(
    ("identifier", "selected", "unused"),
    [
        ("alice_01", "get_by_username", "get_by_email"),
        ("alice@example.com", "get_by_email", "get_by_username"),
    ],
)
@pytest.mark.parametrize("exists", [False, True], ids=["missing-user", "existing-user"])
def test_identifier_selects_and_awaits_only_the_matching_lookup(
    monkeypatch, identifier, selected, unused, exists
):
    repository = UserRepository(Mock(spec=AsyncSession))
    expected = (
        User(username="alice_01", email="alice@example.com", password_hash="test-hash")
        if exists
        else None
    )
    selected_lookup = AsyncMock(return_value=expected)
    unused_lookup = AsyncMock()
    monkeypatch.setattr(repository, selected, selected_lookup)
    monkeypatch.setattr(repository, unused, unused_lookup)

    result = asyncio.run(repository.get_by_identifier(identifier))
    try:
        assert result is expected
        selected_lookup.assert_awaited_once_with(identifier)
        unused_lookup.assert_not_called()
    finally:
        if inspect.iscoroutine(result):
            result.close()


@pytest.mark.parametrize(
    "identifier",
    ["álice_01", "alice_01\u200b", "álice@example.com", "alice@éxample.com"],
)
def test_login_lookup_rejects_collation_aliases(monkeypatch, identifier):
    repository = UserRepository(Mock(spec=AsyncSession))
    stored = User(username="alice_01", email="alice@example.com", password_hash="hash")
    # Simulate MySQL's accent/ignorable-character-insensitive candidate match.
    monkeypatch.setattr(repository, "get_by_username", AsyncMock(return_value=stored))
    monkeypatch.setattr(repository, "get_by_email", AsyncMock(return_value=stored))
    assert asyncio.run(repository.get_by_identifier(identifier)) is None
