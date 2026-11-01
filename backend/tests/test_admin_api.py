from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import require_current_user
from app.api.v1.endpoints import admin
from app.db.session import get_db
from app.main import app
from app.models.user import User
from app.schemas.admin import UserPage
from app.schemas.user import UserResponse


@pytest.fixture
def admin_client():
    client = TestClient(app, raise_server_exceptions=False)
    previous = app.dependency_overrides.copy()
    try:
        yield client
    finally:
        client.close()
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", "/api/v1/admin/users", None),
        ("GET", f"/api/v1/admin/users/{uuid4()}", None),
        ("PATCH", f"/api/v1/admin/users/{uuid4()}/status", {"status": "disabled"}),
    ],
)
def test_admin_endpoints_require_login(admin_client, method, path, body):
    response = admin_client.request(method, path, json=body)
    assert response.status_code == 401


def test_regular_user_cannot_access_admin_endpoints(admin_client):
    async def regular_user():
        return User(
            id=uuid4(),
            username="regular",
            email="regular@example.com",
            role="user",
            status="active",
            password_hash="private",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )

    app.dependency_overrides[require_current_user] = regular_user
    response = admin_client.get("/api/v1/admin/users")
    assert response.status_code == 403
    assert response.json()["code"] == "ADMIN_REQUIRED"


@pytest.fixture
def authenticated_admin(admin_client, monkeypatch):
    user = User(
        id=uuid4(),
        username="admin_01",
        email="admin@example.com",
        role="admin",
        status="active",
        password_hash="private-hash",
        auth_version=3,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    async def identity():
        return user

    session = object()

    async def db():
        yield session

    app.dependency_overrides[require_current_user] = identity
    app.dependency_overrides[get_db] = db
    listed = AsyncMock(
        return_value=UserPage(
            items=[UserResponse.model_validate(user)],
            total=1,
            page=1,
            page_size=20,
        )
    )
    detail, updated = AsyncMock(return_value=user), AsyncMock(return_value=user)
    monkeypatch.setattr(admin.admin, "list_users", listed)
    monkeypatch.setattr(admin.admin, "get_user", detail)
    monkeypatch.setattr(admin.admin, "set_user_status", updated)
    return SimpleNamespace(
        client=admin_client,
        user=user,
        session=session,
        listed=listed,
        detail=detail,
        updated=updated,
    )


def test_admin_list_and_detail_return_only_public_fields(authenticated_admin):
    context = authenticated_admin
    response = context.client.get("/api/v1/admin/users")
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert response.json()["total"] == 1
    assert "password_hash" not in response.text and "auth_version" not in response.text
    context.listed.assert_awaited_once_with(
        context.session,
        page=1,
        page_size=20,
        q=None,
        role=None,
        status=None,
    )
    response = context.client.get(f"/api/v1/admin/users/{context.user.id}")
    assert response.status_code == 200
    context.detail.assert_awaited_once_with(context.user.id, context.session)


@pytest.mark.parametrize(
    "query",
    [
        "page=0",
        "page=-1",
        "page_size=0",
        "page_size=101",
        "role=owner",
        "status=unknown",
        "q=",
        "q=" + "x" * 255,
    ],
)
def test_admin_list_rejects_invalid_query_before_service(authenticated_admin, query):
    context = authenticated_admin
    assert context.client.get("/api/v1/admin/users?" + query).status_code == 422
    context.listed.assert_not_awaited()


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"status": None},
        {"status": "deleted"},
        {"status": "disabled", "role": "admin"},
    ],
)
def test_status_rejects_invalid_or_extra_fields(authenticated_admin, payload):
    context = authenticated_admin
    response = context.client.patch(
        f"/api/v1/admin/users/{context.user.id}/status", json=payload
    )
    assert response.status_code == 422
    context.updated.assert_not_awaited()


def test_status_binds_target_and_validated_status(authenticated_admin):
    context = authenticated_admin
    target = uuid4()
    response = context.client.patch(
        f"/api/v1/admin/users/{target}/status", json={"status": "disabled"}
    )
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    context.updated.assert_awaited_once_with(target, "disabled", context.session)
