import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, Mock
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models.user import User
from app.repositories.admin import AdminRepository


@pytest.fixture
def admin_repository():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as storage:
        for number, username, email, role, status, day in (
            (1, "alice_01", "alice@example.com", "user", "active", 28),
            (2, "alice02", "alice2@example.com", "user", "disabled", 28),
            (3, "alice_admin", "admin@example.com", "admin", "active", 27),
            (4, "other_user", "needle@example.com", "user", "active", 26),
            (5, "percent_user", "literal%tag@example.com", "user", "active", 25),
            (6, "zed", "zed@example.com", "user", "active", 28),
        ):
            storage.add(
                User(
                    id=UUID(int=number),
                    username=username,
                    email=email,
                    password_hash="private-hash",
                    role=role,
                    status=status,
                    auth_version=0,
                    created_at=datetime(2026, 10, day),
                )
            )
        storage.commit()
        session = Mock(spec=AsyncSession)
        session.scalar.side_effect = storage.scalar
        session.scalars.side_effect = storage.scalars
        yield AdminRepository(session), session
    engine.dispose()


def query(repository, **filters):
    return asyncio.run(
        repository.list_users(
            **{"page": 1, "page_size": 20, "q": None, "role": None, "status": None}
            | filters
        )
    )


def test_pagination_counts_all_matches_and_breaks_creation_ties_by_id(admin_repository):
    repository, session = admin_repository
    first, total = query(repository, page_size=2)
    second, second_total = query(repository, page=2, page_size=2)
    third, third_total = query(repository, page=3, page_size=2)
    assert (total, second_total, third_total) == (6, 6, 6)
    assert [user.id.int for user in first + second + third] == [1, 2, 6, 3, 4, 5]
    assert session.scalar.await_count == session.scalars.await_count == 3
    session.commit.assert_not_called()
    session.rollback.assert_not_called()


@pytest.mark.parametrize(
    ("filters", "expected"),
    [
        ({"q": "AlIcE"}, [1, 2, 3]),
        ({"q": "needle@"}, [4]),
        ({"role": "admin"}, [3]),
        ({"status": "disabled"}, [2]),
        ({"q": "alice", "role": "user", "status": "active"}, [1]),
        ({"q": "_"}, [1, 3, 4, 5]),
        ({"q": "%"}, [5]),
        ({"q": r"\_"}, []),
        ({"q": "missing"}, []),
    ],
)
def test_query_filters_are_combined_and_sql_wildcards_are_literal(
    admin_repository, filters, expected
):
    repository, _ = admin_repository
    users, total = query(repository, **filters)
    assert [user.id.int for user in users] == expected
    assert total == len(expected)


def test_page_after_last_result_is_empty_but_retains_filtered_total(admin_repository):
    repository, _ = admin_repository
    users, total = query(repository, q="alice", page=3, page_size=2)
    assert users == []
    assert total == 3


@pytest.mark.parametrize("for_update", [False, True])
@pytest.mark.parametrize("exists", [False, True])
def test_get_user_loads_current_row_with_requested_lock(for_update, exists):
    session = Mock(spec=AsyncSession)
    user_id = uuid4()
    user = User(id=user_id) if exists else None
    session.get = AsyncMock(return_value=user)
    result = asyncio.run(
        AdminRepository(session).get_user(user_id, for_update=for_update)
    )
    assert result is user
    session.get.assert_awaited_once_with(
        User, user_id, populate_existing=True, with_for_update=for_update
    )
    session.commit.assert_not_called()
