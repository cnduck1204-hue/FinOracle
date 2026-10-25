from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.api.v1.endpoints import auth
from app.core.exceptions import AppException
from app.main import app
from app.models.user import User


def valid_payload(**changes):
    return {
        "username": " Alice_01 ",
        "email": "Alice@EXAMPLE.COM",
        "password": " FinOracle密码123 ",
        "confirm_password": " FinOracle密码123 ",
        **changes,
    }


@pytest.fixture
def registration_client(monkeypatch):
    session = object()
    closed_sessions = []
    user = User(
        id=uuid4(),
        username="alice_01",
        email="alice@example.com",
        password_hash="private-argon2-hash-marker",
        role="user",
        status="active",
        last_login_at=None,
        created_at=datetime(2026, 10, 20, 1, 2, 3, 123456),
        updated_at=datetime(2026, 10, 20, 1, 2, 3, 123456),
    )
    service = AsyncMock(return_value=user)
    monkeypatch.setattr(auth, "register_user", service)

    async def override_db():
        try:
            yield session
        finally:
            closed_sessions.append(session)

    previous_overrides = app.dependency_overrides.copy()
    app.dependency_overrides[auth.get_db] = override_db
    client = TestClient(app, raise_server_exceptions=False)
    try:
        yield SimpleNamespace(
            client=client,
            session=session,
            closed_sessions=closed_sessions,
            user=user,
            service=service,
        )
    finally:
        client.close()
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous_overrides)


def test_public_registration_returns_safe_user_and_normalized_input(
    registration_client, caplog
):
    caplog.set_level("INFO", logger="app.middleware.request_context")
    context = registration_client
    response = context.client.post(
        "/api/v1/auth/register",
        json=valid_payload(),
        headers={"X-Request-ID": "register-success-test"},
    )

    assert response.status_code == 201
    assert response.headers["X-Request-ID"] == "register-success-test"
    assert response.json() == {
        "id": str(context.user.id),
        "username": "alice_01",
        "email": "alice@example.com",
        "role": "user",
        "status": "active",
        "last_login_at": None,
        "created_at": "2026-10-20T01:02:03.123456Z",
        "updated_at": "2026-10-20T01:02:03.123456Z",
    }
    context.service.assert_awaited_once()
    supplied, session = context.service.await_args.args
    assert session is context.session
    assert supplied.username == "alice_01"
    assert supplied.email == "alice@example.com"
    assert supplied.password.get_secret_value() == " FinOracle密码123 "
    assert context.user.created_at.tzinfo is None
    assert context.closed_sessions == [context.session]
    for private_value in ["FinOracle密码123", "private-argon2-hash-marker"]:
        assert private_value not in response.text
        assert private_value not in caplog.text


@pytest.mark.parametrize(
    "code", ["USER_USERNAME_EXISTS", "USER_EMAIL_EXISTS", "USER_ALREADY_EXISTS"]
)
def test_conflicts_keep_business_error_and_request_id(registration_client, code):
    context = registration_client
    context.service.side_effect = AppException(
        code=code, message="账号已存在", status_code=409
    )

    response = context.client.post(
        "/api/v1/auth/register",
        json=valid_payload(),
        headers={"X-Request-ID": "register-conflict-test"},
    )

    assert response.status_code == 409
    assert response.headers["X-Request-ID"] == "register-conflict-test"
    assert response.json() == {
        "code": code,
        "message": "账号已存在",
        "request_id": "register-conflict-test",
    }
    assert context.closed_sessions == [context.session]


@pytest.mark.parametrize(
    "changes",
    [
        {"username": "bad-name"},
        {"email": "invalid-email"},
        {"password": "abcdefgh", "confirm_password": "abcdefgh"},
        {"confirm_password": "Different123"},
        {"role": "admin"},
        {"status": "disabled"},
        {"password_hash": "client-provided-hash"},
        {"id": str(uuid4())},
    ],
)
def test_invalid_or_privileged_input_never_enters_service(
    registration_client, changes, caplog
):
    caplog.set_level("INFO", logger="app.middleware.request_context")
    context = registration_client
    response = context.client.post(
        "/api/v1/auth/register",
        json=valid_payload(**changes),
        headers={"X-Request-ID": "register-validation-test"},
    )

    assert response.status_code == 422
    assert response.json() == {
        "code": "REQUEST_VALIDATION_ERROR",
        "message": "请求参数不合法",
        "request_id": "register-validation-test",
    }
    context.service.assert_not_awaited()
    assert "FinOracle密码123" not in response.text
    assert "FinOracle密码123" not in caplog.text


def test_malformed_json_is_a_safe_validation_error(registration_client):
    context = registration_client
    response = context.client.post(
        "/api/v1/auth/register",
        content="{",
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 422
    assert response.json()["code"] == "REQUEST_VALIDATION_ERROR"
    context.service.assert_not_awaited()


def test_unexpected_registration_failure_returns_safe_json(registration_client):
    context = registration_client
    context.service.side_effect = RuntimeError("private-internal-error-marker")
    response = context.client.post(
        "/api/v1/auth/register",
        json=valid_payload(),
        headers={"X-Request-ID": "register-failure-test"},
    )

    assert response.status_code == 500
    assert response.json() == {
        "code": "INTERNAL_SERVER_ERROR",
        "message": "服务器内部错误",
        "request_id": "register-failure-test",
    }
    assert "private-internal-error-marker" not in response.text
    assert context.closed_sessions == [context.session]


def test_openapi_documents_registration_and_excludes_private_response_fields():
    schema = app.openapi()
    operation = schema["paths"]["/api/v1/auth/register"]["post"]
    assert "201" in operation["responses"]
    assert not operation.get("security")
    response_schema = operation["responses"]["201"]["content"]["application/json"][
        "schema"
    ]
    assert response_schema["$ref"].endswith("/UserResponse")
    user_fields = schema["components"]["schemas"]["UserResponse"]["properties"]
    assert {"password", "confirm_password", "password_hash"}.isdisjoint(user_fields)
    request_fields = schema["components"]["schemas"]["RegisterRequest"]["properties"]
    for field in ["password", "confirm_password"]:
        assert request_fields[field]["writeOnly"] is True
        assert request_fields[field]["format"] == "password"
