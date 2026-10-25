from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.api.v1.endpoints import auth
from app.core.exceptions import AppException
from app.main import app
from app.schemas.auth import LoginResponse
from app.schemas.user import UserResponse


@pytest.fixture
def lifecycle_client(monkeypatch):
    now = datetime(2026, 10, 25, tzinfo=UTC)
    result = LoginResponse(
        access_token="new-access-marker",
        refresh_token="new-refresh-marker",
        expires_in=1800,
        user=UserResponse(
            id=uuid4(),
            username="alice_01",
            email="alice@example.com",
            role="user",
            status="active",
            last_login_at=now,
            created_at=now,
            updated_at=now,
        ),
    )
    renew = AsyncMock(return_value=result)
    logout = AsyncMock(return_value=None)
    monkeypatch.setattr(auth, "refresh_tokens", renew, raising=False)
    monkeypatch.setattr(auth, "logout_user", logout, raising=False)
    session, cache = object(), object()

    async def db():
        yield session

    async def redis():
        return cache

    previous = app.dependency_overrides.copy()
    app.dependency_overrides[auth.get_db] = db
    app.dependency_overrides[auth.get_redis] = redis
    client = TestClient(app, raise_server_exceptions=False)
    try:
        yield SimpleNamespace(
            client=client,
            renew=renew,
            logout=logout,
            result=result,
            session=session,
            cache=cache,
        )
    finally:
        client.close()
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)


def test_refresh_accepts_json_without_access_token_and_disables_caching(
    lifecycle_client, caplog
):
    context = lifecycle_client
    response = context.client.post(
        "/api/v1/auth/refresh", json={"refresh_token": "old-refresh-marker"}
    )
    assert response.status_code == 200
    assert response.json() == context.result.model_dump(mode="json")
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Pragma"] == "no-cache"
    context.renew.assert_awaited_once_with(
        "old-refresh-marker", context.session, context.cache
    )
    for token in ["old-refresh-marker", "new-refresh-marker", "new-access-marker"]:
        assert token not in caplog.text


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"refresh_token": ""},
        {"refresh_token": None},
        {"refresh_token": 123},
        {"refresh_token": "x" * 4097},
        {"refresh_token": "private-marker", "role": "admin"},
    ],
)
def test_refresh_rejects_invalid_input_before_service(lifecycle_client, payload):
    response = lifecycle_client.client.post("/api/v1/auth/refresh", json=payload)
    assert response.status_code == 422
    assert response.json()["code"] == "REQUEST_VALIDATION_ERROR"
    assert "private-marker" not in response.text
    lifecycle_client.renew.assert_not_awaited()


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (401, "AUTH_INVALID_TOKEN"),
        (403, "USER_DISABLED"),
        (503, "AUTH_SERVICE_UNAVAILABLE"),
    ],
)
def test_refresh_preserves_safe_business_errors(lifecycle_client, status, code):
    lifecycle_client.renew.side_effect = AppException(
        code=code, message="认证失败", status_code=status
    )
    response = lifecycle_client.client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": "private-marker"},
        headers={"X-Request-ID": "refresh-error"},
    )
    assert response.status_code == status
    assert response.json() == {
        "code": code,
        "message": "认证失败",
        "request_id": "refresh-error",
    }
    assert "private-marker" not in response.text


@pytest.mark.parametrize("path", ["logout", "logout-all"])
def test_logout_requires_bearer_credentials(lifecycle_client, path):
    response = lifecycle_client.client.post(f"/api/v1/auth/{path}")
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    lifecycle_client.logout.assert_not_awaited()


@pytest.mark.parametrize("path", ["logout", "logout-all"])
def test_logout_has_empty_204_response_and_correct_scope(lifecycle_client, path):
    context = lifecycle_client
    response = context.client.post(
        f"/api/v1/auth/{path}", headers={"Authorization": "Bearer access-marker"}
    )
    assert response.status_code == 204 and response.content == b""
    context.logout.assert_awaited_once_with(
        "access-marker",
        context.session,
        context.cache,
        all_sessions=path == "logout-all",
    )


@pytest.mark.parametrize("path", ["logout", "logout-all"])
def test_logout_does_not_report_success_when_storage_fails(lifecycle_client, path):
    lifecycle_client.logout.side_effect = AppException(
        code="AUTH_SERVICE_UNAVAILABLE", message="认证服务暂时不可用", status_code=503
    )
    response = lifecycle_client.client.post(
        f"/api/v1/auth/{path}", headers={"Authorization": "Bearer access-marker"}
    )
    assert response.status_code == 503
    assert response.json()["code"] == "AUTH_SERVICE_UNAVAILABLE"


def test_openapi_marks_logout_as_authenticated_but_refresh_as_public():
    schema = app.openapi()
    assert not schema["paths"]["/api/v1/auth/refresh"]["post"].get("security")
    for path in ["logout", "logout-all"]:
        assert {"AccessToken": []} in schema["paths"][f"/api/v1/auth/{path}"]["post"][
            "security"
        ]
