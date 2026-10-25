import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError

from app.api.v1.endpoints import health
from app.main import app


@pytest.fixture
def readiness_client(monkeypatch):
    db = SimpleNamespace(scalar=AsyncMock(return_value=1))
    cache = SimpleNamespace(ping=AsyncMock(return_value=True))

    async def override_db():
        return db

    async def override_cache():
        return cache

    previous_overrides = app.dependency_overrides.copy()
    app.dependency_overrides[health.get_db] = override_db
    app.dependency_overrides[health.get_redis] = override_cache
    monkeypatch.setattr(health, "DEPENDENCY_TIMEOUT_SECONDS", 0.02)
    client = TestClient(app, raise_server_exceptions=False)
    try:
        yield client, db, cache
    finally:
        client.close()
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous_overrides)


def assert_unavailable(response):
    assert response.status_code == 503
    assert response.json() == {
        "code": "DEPENDENCY_UNAVAILABLE",
        "message": "服务暂未就绪，请稍后重试",
        "request_id": response.headers["X-Request-ID"],
    }


def test_ready_checks_both_dependencies(readiness_client):
    client, db, cache = readiness_client
    response = client.get("/api/v1/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "FinOracle",
        "dependencies": {"mysql": "ok", "redis": "ok"},
    }
    db.scalar.assert_awaited_once()
    cache.ping.assert_awaited_once()


@pytest.mark.parametrize("dependency", ["mysql", "redis"])
def test_dependency_errors_return_503_without_internal_details(
    readiness_client, dependency
):
    client, db, cache = readiness_client
    secret_marker = "private-password-and-internal-sql"
    if dependency == "mysql":
        db.scalar.side_effect = SQLAlchemyError(secret_marker)
    else:
        cache.ping.side_effect = RedisError(secret_marker)

    response = client.get("/api/v1/ready", headers={"X-Request-ID": "ready-error-test"})

    assert_unavailable(response)
    assert response.json()["request_id"] == "ready-error-test"
    assert secret_marker not in response.text


@pytest.mark.parametrize("dependency", ["mysql", "redis"])
def test_timeout_cancels_check_and_returns_503(readiness_client, dependency):
    client, db, cache = readiness_client
    cancelled = []

    async def blocked(*args, **kwargs):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.append(True)

    if dependency == "mysql":
        db.scalar.side_effect = blocked
    else:
        cache.ping.side_effect = blocked

    response = client.get("/api/v1/ready")

    assert_unavailable(response)
    assert cancelled == [True]


@pytest.mark.parametrize("mysql_result,redis_result", [(0, True), (1, False), (1, 1)])
def test_unsuccessful_dependency_results_return_503(
    readiness_client, mysql_result, redis_result
):
    client, db, cache = readiness_client
    db.scalar.return_value = mysql_result
    cache.ping.return_value = redis_result

    assert_unavailable(client.get("/api/v1/ready"))


def test_health_does_not_query_unavailable_dependencies(readiness_client):
    client, db, cache = readiness_client
    db.scalar.side_effect = SQLAlchemyError("MySQL unavailable")
    cache.ping.side_effect = RedisError("Redis unavailable")

    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "FinOracle"}
    db.scalar.assert_not_awaited()
    cache.ping.assert_not_awaited()


def test_unexpected_error_uses_safe_json_response(readiness_client):
    client, db, _ = readiness_client
    secret_marker = "private-unexpected-error-details"
    db.scalar.side_effect = RuntimeError(secret_marker)

    response = client.get(
        "/api/v1/ready", headers={"X-Request-ID": "ready-unknown-test"}
    )

    assert response.status_code == 500
    assert response.headers["content-type"].startswith("application/json")
    assert response.json() == {
        "code": "INTERNAL_SERVER_ERROR",
        "message": "服务器内部错误",
        "request_id": "ready-unknown-test",
    }
    assert secret_marker not in response.text
