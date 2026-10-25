import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

import app.main as main_module
from app.db import session as database


def test_session_factory_can_create_a_session():
    async def check():
        async with database.SessionFactory() as session:
            assert isinstance(session, AsyncSession)
            assert session.sync_session.expire_on_commit is False

    asyncio.run(check())


@pytest.mark.parametrize("request_fails", [False, True])
def test_dependency_closes_session_and_propagates_errors(monkeypatch, request_fails):
    session = AsyncSession()
    close = AsyncMock(wraps=session.close)
    monkeypatch.setattr(session, "close", close)
    monkeypatch.setattr(database, "SessionFactory", lambda: session)

    async def check():
        dependency = database.get_db()
        try:
            assert await anext(dependency) is session
            close.assert_not_awaited()
            if request_fails:
                error = RuntimeError("request failed")
                with pytest.raises(RuntimeError) as caught:
                    await dependency.athrow(error)
                assert caught.value is error
            else:
                with pytest.raises(StopAsyncIteration):
                    await anext(dependency)
        finally:
            await dependency.aclose()

    asyncio.run(check())
    close.assert_awaited_once()


def test_application_shutdown_closes_redis_and_disposes_engine(monkeypatch):
    dispose = AsyncMock()
    close = AsyncMock()
    monkeypatch.setattr(main_module, "engine", SimpleNamespace(dispose=dispose))
    monkeypatch.setattr(main_module, "redis_client", SimpleNamespace(aclose=close))

    with TestClient(main_module.app) as client:
        response = client.get("/api/v1/health")
        assert response.status_code == 200
        dispose.assert_not_awaited()
        close.assert_not_awaited()

    dispose.assert_awaited_once_with()
    close.assert_awaited_once_with()


def test_lifespan_disposes_engine_after_an_error(monkeypatch):
    dispose = AsyncMock()
    close = AsyncMock()
    monkeypatch.setattr(main_module, "engine", SimpleNamespace(dispose=dispose))
    monkeypatch.setattr(main_module, "redis_client", SimpleNamespace(aclose=close))

    async def check():
        error = RuntimeError("application failed")
        with pytest.raises(RuntimeError) as caught:
            async with main_module.lifespan(main_module.app):
                raise error
        assert caught.value is error

    asyncio.run(check())
    dispose.assert_awaited_once_with()
    close.assert_awaited_once_with()


def test_redis_cleanup_failure_does_not_skip_database_cleanup(monkeypatch):
    error = RuntimeError("Redis cleanup failed")
    dispose = AsyncMock()
    close = AsyncMock(side_effect=error)
    monkeypatch.setattr(main_module, "engine", SimpleNamespace(dispose=dispose))
    monkeypatch.setattr(main_module, "redis_client", SimpleNamespace(aclose=close))

    async def check():
        with pytest.raises(RuntimeError) as caught:
            async with main_module.lifespan(main_module.app):
                pass
        assert caught.value is error

    asyncio.run(check())
    close.assert_awaited_once_with()
    dispose.assert_awaited_once_with()
