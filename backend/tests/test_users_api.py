import asyncio
from datetime import datetime
from time import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import jwt
import pytest
from fastapi.testclient import TestClient
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import dependencies
from app.core.config import Settings
from app.main import app
from app.models.user import User
from app.schemas.session import SessionRecord
from app.schemas.user import UserResponse
from app.security import tokens
from app.security.tokens import create_token, decode_token
from app.services import current_user

TEST_KEY = "d98f15c0e2b64aa7b91d0438f5c6e270a18b3f9d42e65c07b6a1d8e34f9205ac"


@pytest.fixture
def users_client(monkeypatch):
    settings = Settings(_env_file=None, jwt_secret_key=TEST_KEY)
    monkeypatch.setattr(tokens, "get_settings", lambda: settings)
    now = datetime(2026, 10, 22, 1, 2, 3, 123456)
    user = User(
        id=uuid4(),
        username="alice_01",
        email="alice@example.com",
        password_hash="private-users-hash-marker",
        role="admin",
        status="active",
        last_login_at=now,
        created_at=now,
        updated_at=now,
    )
    token = create_token(
        user_id=user.id,
        role="user",
        session_id=uuid4(),
        token_id=uuid4(),
        token_type="access",
    )
    claims = decode_token(token, expected_type="access")
    record = SessionRecord(
        id=claims.sid,
        user_id=user.id,
        refresh_jti=uuid4(),
        created_at=claims.iat,
        expires_at=claims.exp + 3600,
    )
    cache = Mock(spec=Redis)
    cache.get = AsyncMock(return_value=record.model_dump_json())
    session = AsyncSession(expire_on_commit=False)
    lookup = AsyncMock(return_value=user)
    monkeypatch.setattr(
        current_user,
        "UserRepository",
        Mock(return_value=SimpleNamespace(get_by_id=lookup)),
    )
    closed_sessions = []

    async def override_db():
        try:
            yield session
        finally:
            closed_sessions.append(session)

    async def override_cache():
        return cache

    previous_overrides = app.dependency_overrides.copy()
    app.dependency_overrides[dependencies.get_db] = override_db
    app.dependency_overrides[dependencies.get_redis] = override_cache
    client = TestClient(app, raise_server_exceptions=False)
    try:
        yield SimpleNamespace(
            client=client,
            user=user,
            token=token,
            claims=claims,
            record=record,
            session=session,
            cache=cache,
            lookup=lookup,
            closed_sessions=closed_sessions,
        )
    finally:
        client.close()
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous_overrides)
        asyncio.run(session.close())


def get_me(context, authorization=None, **kwargs):
    headers = {"X-Request-ID": "users-me-test"}
    if authorization is not None:
        headers["Authorization"] = authorization
    return context.client.get("/api/v1/users/me", headers=headers, **kwargs)


def assert_error(response, status_code, code, message):
    assert response.status_code == status_code
    assert response.json() == {
        "code": code,
        "message": message,
        "request_id": "users-me-test",
    }
    if status_code != 500:
        assert response.headers["X-Request-ID"] == "users-me-test"
    if status_code == 401:
        assert response.headers["WWW-Authenticate"] == "Bearer"
    else:
        assert "WWW-Authenticate" not in response.headers


@pytest.mark.parametrize("scheme", ["Bearer", "bearer"])
def test_me_uses_real_authentication_and_returns_only_safe_current_fields(
    users_client, caplog, scheme
):
    context = users_client
    caplog.set_level("INFO", logger="app.middleware.request_context")
    response = get_me(context, f"{scheme} {context.token}")

    assert response.status_code == 200
    assert response.json() == UserResponse.model_validate(context.user).model_dump(
        mode="json"
    )
    assert response.json()["role"] == "admin" and context.claims.role == "user"
    assert response.json()["last_login_at"] == "2026-10-22T01:02:03.123456Z"
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["X-Request-ID"] == "users-me-test"
    assert context.closed_sessions == [context.session]
    context.lookup.assert_awaited_once_with(context.user.id)
    assert not context.session.in_transaction()
    for secret in [context.user.password_hash, context.token]:
        assert secret not in response.text and secret not in caplog.text


@pytest.mark.parametrize(
    "authorization", [None, "", "Bearer", "Bearer ", "Basic abc123"]
)
def test_missing_or_wrong_auth_scheme_is_rejected_before_storage(
    users_client, authorization
):
    context = users_client
    response = get_me(context, authorization)
    assert_error(response, 401, "AUTH_REQUIRED", "请先登录")
    context.cache.get.assert_not_awaited()
    context.lookup.assert_not_awaited()


def test_query_parameter_token_does_not_bypass_header_requirement(users_client):
    context = users_client
    response = get_me(context, params={"access_token": context.token})
    assert_error(response, 401, "AUTH_REQUIRED", "请先登录")
    context.cache.get.assert_not_awaited()


@pytest.mark.parametrize("mode", ["malformed", "refresh", "expired"])
def test_invalid_token_types_or_expiration_return_401_without_storage(
    users_client, mode
):
    context = users_client
    token = "malformed-access-token-marker"
    if mode == "refresh":
        token = create_token(
            user_id=context.user.id,
            role="user",
            session_id=context.claims.sid,
            token_id=context.record.refresh_jti,
            token_type="refresh",
        )
    elif mode == "expired":
        payload = context.claims.model_dump(mode="json")
        payload.update(iat=int(time()) - 120, exp=int(time()) - 60)
        token = jwt.encode(payload, TEST_KEY, algorithm="HS256")
    response = get_me(context, f"Bearer {token}")
    assert_error(response, 401, "AUTH_INVALID_TOKEN", "令牌无效或已过期")
    context.cache.get.assert_not_awaited()
    context.lookup.assert_not_awaited()


@pytest.mark.parametrize("mode", ["missing", "revoked", "other-user", "expired"])
def test_invalid_sessions_return_401_without_loading_user(users_client, mode):
    context = users_client
    if mode == "missing":
        context.cache.get.return_value = None
    else:
        changes = {"revoked": True}
        if mode == "other-user":
            changes = {"user_id": uuid4()}
        elif mode == "expired":
            changes = {"created_at": int(time()) - 60, "expires_at": int(time()) - 1}
        context.cache.get.return_value = context.record.model_copy(
            update=changes
        ).model_dump_json()
    response = get_me(context, f"Bearer {context.token}")
    assert_error(response, 401, "AUTH_INVALID_TOKEN", "令牌无效或已过期")
    context.lookup.assert_not_awaited()


@pytest.mark.parametrize("mode", ["deleted", "disabled"])
def test_current_database_state_controls_access(users_client, mode):
    context = users_client
    if mode == "deleted":
        context.lookup.return_value = None
    else:
        context.user.status = "disabled"
    response = get_me(context, f"Bearer {context.token}")
    if mode == "deleted":
        assert_error(response, 401, "AUTH_INVALID_TOKEN", "令牌无效或已过期")
    else:
        assert_error(response, 403, "USER_DISABLED", "账号已被禁用")
    assert not context.session.in_transaction()


@pytest.mark.parametrize("mode", ["redis", "corrupt-session", "database"])
def test_dependency_failures_return_safe_503_not_401(users_client, mode):
    context = users_client
    if mode == "redis":
        context.cache.get.side_effect = RedisError("private-infrastructure-marker")
    elif mode == "corrupt-session":
        context.cache.get.return_value = "{private-infrastructure-marker"
    else:
        context.lookup.side_effect = OperationalError(
            "SELECT", {}, Exception(2006, "private-infrastructure-marker")
        )
    response = get_me(context, f"Bearer {context.token}")
    assert_error(
        response, 503, "AUTH_SERVICE_UNAVAILABLE", "认证服务暂时不可用，请稍后重试"
    )
    assert "private-infrastructure-marker" not in response.text
    assert context.closed_sessions == [context.session]
    assert not context.session.in_transaction()


def test_unexpected_failure_returns_safe_500(users_client):
    context = users_client
    context.lookup.side_effect = RuntimeError("private-programming-error-marker")
    response = get_me(context, f"Bearer {context.token}")
    assert_error(response, 500, "INTERNAL_SERVER_ERROR", "服务器内部错误")
    assert "private-programming-error-marker" not in response.text
    assert context.closed_sessions == [context.session]


def test_openapi_documents_bearer_authentication_for_me_only():
    schema = app.openapi()
    operation = schema["paths"]["/api/v1/users/me"]["get"]
    assert {"AccessToken": []} in operation["security"]
    security = schema["components"]["securitySchemes"]["AccessToken"]
    assert security["type"] == "http" and security["scheme"] == "bearer"
    assert operation["responses"]["200"]["content"]["application/json"]["schema"][
        "$ref"
    ].endswith("/UserResponse")
    for path in ["/api/v1/auth/register", "/api/v1/auth/login"]:
        assert not schema["paths"][path]["post"].get("security")
