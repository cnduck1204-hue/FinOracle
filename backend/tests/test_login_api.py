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


def valid_payload(**changes):
    return {"identifier": " Alice_01 ", "password": " Private密码123 ", **changes}


@pytest.fixture
def login_client(monkeypatch):
    session, cache = object(), object()
    closed_sessions = []
    now = datetime(2026, 10, 20, 1, 2, 3, 123456, tzinfo=UTC)
    result = LoginResponse(
        access_token="private-access-token-marker",
        refresh_token="private-refresh-token-marker",
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
    service = AsyncMock(return_value=result)
    monkeypatch.setattr(auth, "login_user", service)

    async def override_db():
        try:
            yield session
        finally:
            closed_sessions.append(session)

    async def override_cache():
        return cache

    previous_overrides = app.dependency_overrides.copy()
    app.dependency_overrides[auth.get_db] = override_db
    app.dependency_overrides[auth.get_redis] = override_cache
    client = TestClient(app, raise_server_exceptions=False)
    try:
        yield SimpleNamespace(
            client=client,
            session=session,
            cache=cache,
            service=service,
            result=result,
            closed_sessions=closed_sessions,
        )
    finally:
        client.close()
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous_overrides)


def test_login_returns_tokens_with_no_cache_and_safe_profile(login_client, caplog):
    caplog.set_level("INFO", logger="app.middleware.request_context")
    context = login_client
    response = context.client.post(
        "/api/v1/auth/login",
        json=valid_payload(),
        headers={"X-Request-ID": "login-success-test"},
    )

    assert response.status_code == 200
    assert response.json() == context.result.model_dump(mode="json")
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Pragma"] == "no-cache"
    assert response.headers["X-Request-ID"] == "login-success-test"
    context.service.assert_awaited_once()
    payload, session, cache = context.service.await_args.args
    assert payload.identifier == "alice_01"
    assert payload.password.get_secret_value() == " Private密码123 "
    assert session is context.session and cache is context.cache
    assert context.closed_sessions == [context.session]
    assert "Private密码123" not in response.text
    assert set(response.json()["user"]) == {
        "id",
        "username",
        "email",
        "role",
        "status",
        "last_login_at",
        "created_at",
        "updated_at",
    }
    for private in [
        "Private密码123",
        context.result.access_token,
        context.result.refresh_token,
    ]:
        assert private not in caplog.text


@pytest.mark.parametrize(
    ("status_code", "code", "message"),
    [
        (401, "AUTH_INVALID_CREDENTIALS", "账号或密码错误"),
        (403, "USER_DISABLED", "账号已被禁用"),
        (429, "AUTH_LOGIN_RATE_LIMITED", "登录失败次数过多，请稍后重试"),
        (503, "AUTH_SERVICE_UNAVAILABLE", "认证服务暂时不可用，请稍后重试"),
    ],
)
def test_login_preserves_service_errors_without_tokens(
    login_client, status_code, code, message
):
    context = login_client
    context.service.side_effect = AppException(
        code=code, message=message, status_code=status_code
    )
    response = context.client.post(
        "/api/v1/auth/login",
        json=valid_payload(),
        headers={"X-Request-ID": "login-error-test"},
    )

    assert response.status_code == status_code
    assert response.json() == {
        "code": code,
        "message": message,
        "request_id": "login-error-test",
    }
    assert response.headers["X-Request-ID"] == "login-error-test"
    assert context.closed_sessions == [context.session]


@pytest.mark.parametrize(
    "payload",
    [
        {},
        valid_payload(identifier="  "),
        valid_payload(identifier="a" * 255),
        valid_payload(password=""),
        valid_payload(password="a" * 129),
        valid_payload(password=12345),
        valid_payload(role="admin"),
        valid_payload(refresh_token="untrusted-token-marker"),
    ],
)
def test_invalid_login_never_calls_service(login_client, payload, caplog):
    context = login_client
    response = context.client.post("/api/v1/auth/login", json=payload)

    assert response.status_code == 422
    assert response.json()["code"] == "REQUEST_VALIDATION_ERROR"
    assert "Private密码123" not in response.text
    assert "Private密码123" not in caplog.text
    context.service.assert_not_awaited()


def test_malformed_json_never_calls_service(login_client):
    response = login_client.client.post(
        "/api/v1/auth/login",
        content="{",
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 422
    assert response.json()["code"] == "REQUEST_VALIDATION_ERROR"
    login_client.service.assert_not_awaited()


def test_unexpected_login_failure_does_not_expose_details(login_client):
    context = login_client
    context.service.side_effect = RuntimeError("private-login-error-marker")
    response = context.client.post(
        "/api/v1/auth/login",
        json=valid_payload(),
        headers={"X-Request-ID": "login-failure-test"},
    )
    assert response.status_code == 500
    assert response.json() == {
        "code": "INTERNAL_SERVER_ERROR",
        "message": "服务器内部错误",
        "request_id": "login-failure-test",
    }
    assert "private-login-error-marker" not in response.text
    assert context.closed_sessions == [context.session]


def test_login_openapi_documents_public_json_endpoint():
    schema = app.openapi()
    operation = schema["paths"]["/api/v1/auth/login"]["post"]
    assert not operation.get("security")
    assert "application/json" in operation["requestBody"]["content"]
    assert operation["responses"]["200"]["content"]["application/json"]["schema"][
        "$ref"
    ].endswith("/LoginResponse")
    fields = schema["components"]["schemas"]["LoginResponse"]["properties"]
    assert set(fields) == {
        "access_token",
        "refresh_token",
        "token_type",
        "expires_in",
        "user",
    }
    password = schema["components"]["schemas"]["LoginRequest"]["properties"]["password"]
    assert password["writeOnly"] is True and password["format"] == "password"
