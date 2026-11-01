from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app.api.dependencies import require_current_user
from app.api.v1.endpoints import users
from app.core.exceptions import AppException
from app.db.session import get_db
from app.main import app
from app.models.user import User
from app.schemas.user import UserResponse


@pytest.fixture
def profile_client(monkeypatch):
    user = User(
        id=uuid4(),
        username="alice",
        email="alice@example.com",
        password_hash="private-profile-hash-marker",
        role="user",
        status="active",
        created_at=datetime(2026, 1, 1),
        updated_at=datetime(2026, 10, 28),
    )
    session = object()
    service = AsyncMock(return_value=user)
    monkeypatch.setattr(users, "update_profile", service, raising=False)

    async def current_user():
        return user

    async def database():
        yield session

    previous = app.dependency_overrides.copy()
    app.dependency_overrides[require_current_user] = current_user
    app.dependency_overrides[get_db] = database
    with TestClient(app, raise_server_exceptions=False) as client:
        yield SimpleNamespace(
            client=client, user=user, session=session, service=service
        )
    app.dependency_overrides.clear()
    app.dependency_overrides.update(previous)


def patch(context, **kwargs):
    return context.client.patch(
        "/api/v1/users/me", headers={"X-Request-ID": "profile-test"}, **kwargs
    )


def test_patch_returns_safe_latest_current_profile(profile_client, caplog):
    context = profile_client
    latest = User(
        id=context.user.id,
        username="bob",
        email=context.user.email,
        password_hash=context.user.password_hash,
        role=context.user.role,
        status=context.user.status,
        created_at=context.user.created_at,
        updated_at=datetime(2026, 10, 28, 1, 2, 3),
    )
    context.service.return_value = latest
    response = patch(context, json={"username": " Bob "})
    assert response.status_code == 200
    assert response.json() == UserResponse.model_validate(latest).model_dump(
        mode="json"
    )
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["X-Request-ID"] == "profile-test"
    payload, user_id, session = context.service.await_args.args
    assert payload.model_dump(exclude_unset=True) == {"username": "bob"}
    assert user_id == context.user.id and session is context.session
    assert context.user.password_hash not in response.text + caplog.text


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"username": None},
        {"email": "invalid"},
        {"username": "bob", "role": "admin"},
        {"username": "bob", "user_id": str(uuid4())},
    ],
)
def test_patch_invalid_body_never_reaches_service(profile_client, body):
    response = patch(profile_client, json=body)
    assert response.status_code == 422
    assert response.json()["code"] == "REQUEST_VALIDATION_ERROR"
    profile_client.service.assert_not_awaited()


def test_patch_malformed_json_is_rejected(profile_client):
    response = patch(profile_client, content='{"private-marker":')
    assert response.status_code == 422
    assert "private-marker" not in response.text
    profile_client.service.assert_not_awaited()


def test_patch_requires_authentication(profile_client):
    app.dependency_overrides.pop(require_current_user)
    response = patch(profile_client, json={"username": "bob"})
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    profile_client.service.assert_not_awaited()


@pytest.mark.parametrize(
    "failure",
    [
        AppException(
            code="USER_ALREADY_EXISTS", message="用户名或邮箱已存在", status_code=409
        ),
        OperationalError("UPDATE", {}, Exception("private-db-marker")),
    ],
)
def test_patch_errors_have_safe_existing_envelope(profile_client, failure):
    profile_client.service.side_effect = failure
    response = patch(profile_client, json={"username": "bob"})
    assert response.status_code == (409 if isinstance(failure, AppException) else 500)
    assert set(response.json()) == {"code", "message", "request_id"}
    assert "private-db-marker" not in response.text


def test_patch_openapi_requires_bearer_and_returns_user_response():
    operation = app.openapi()["paths"]["/api/v1/users/me"]["patch"]
    assert {"AccessToken": []} in operation["security"]
    assert operation["responses"]["200"]["content"]["application/json"]["schema"][
        "$ref"
    ].endswith("/UserResponse")
