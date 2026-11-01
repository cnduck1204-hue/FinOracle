import pytest
from pydantic import ValidationError


def test_profile_normalizes_identifiers_like_registration():
    from app.schemas.profile import ProfileUpdateRequest

    payload = ProfileUpdateRequest(username=" Alice_01 ", email=" Alice@EXAMPLE.COM ")
    assert payload.model_dump(exclude_unset=True) == {
        "username": "alice_01",
        "email": "alice@example.com",
    }


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"username": None},
        {"email": None},
        {"username": "alice", "email": None},
        {"username": "ab"},
        {"username": "a" * 33},
        {"username": "alice@example.com"},
        {"username": "张三"},
        {"username": 123},
        {"email": "invalid"},
        {"email": "a" * 255 + "@example.com"},
        {"username": "alice", "role": "admin"},
        {"username": "alice", "status": "active"},
        {"username": "alice", "password": "Secret123"},
        {"username": "alice", "password_hash": "private-marker"},
        {"username": "alice", "id": "another-user"},
        {"username": "alice", "auth_version": 0},
        {"username": "alice", "created_at": "2026-01-01"},
    ],
)
def test_profile_rejects_invalid_or_protected_fields(data):
    from app.schemas.profile import ProfileUpdateRequest

    with pytest.raises(ValidationError):
        ProfileUpdateRequest.model_validate(data)


@pytest.mark.parametrize("data", [{"username": "alice"}, {"email": "a@example.com"}])
def test_profile_preserves_omitted_fields(data):
    from app.schemas.profile import ProfileUpdateRequest

    assert (
        ProfileUpdateRequest.model_validate(data).model_dump(exclude_unset=True) == data
    )
