from datetime import UTC, datetime, timedelta, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.models.user import User
from app.schemas.user import UserResponse


@pytest.mark.parametrize(
    "stored_time",
    [
        datetime(2026, 10, 20, 0, 0, 0, 123456),
        datetime(2026, 10, 20, 0, 0, 0, 123456, tzinfo=UTC),
        datetime(2026, 10, 20, 8, 0, 0, 123456, tzinfo=timezone(timedelta(hours=8))),
    ],
    ids=["naive-utc", "aware-utc", "aware-offset"],
)
def test_response_normalizes_all_timestamps_without_changing_orm_object(stored_time):
    user = User(
        id=uuid4(),
        username="alice_01",
        email="alice@example.com",
        password_hash="private-hash",
        role="user",
        status="active",
        last_login_at=stored_time,
        created_at=stored_time,
        updated_at=stored_time,
    )

    response = UserResponse.model_validate(user)
    expected = datetime(2026, 10, 20, 0, 0, 0, 123456, tzinfo=UTC)
    for field in ["last_login_at", "created_at", "updated_at"]:
        assert getattr(response, field) == expected
        assert getattr(response, field).tzinfo is UTC
        assert getattr(user, field) is stored_time
        assert response.model_dump(mode="json")[field] == "2026-10-20T00:00:00.123456Z"
    assert "private-hash" not in response.model_dump_json()


@pytest.mark.parametrize("changes", [{"role": "owner"}, {"status": "pending"}])
def test_response_rejects_unknown_roles_and_statuses(changes):
    payload = {
        "id": uuid4(),
        "username": "alice_01",
        "email": "alice@example.com",
        "role": "user",
        "status": "active",
        "last_login_at": None,
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
        **changes,
    }

    with pytest.raises(ValidationError):
        UserResponse.model_validate(payload)
