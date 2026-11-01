import json
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from scripts import benchmark


def test_probe_requires_opt_in_and_rejects_nonlocal_or_credential_urls():
    for url in (
        "https://example.com/api/v1",
        "http://secret@127.0.0.1:8001/api/v1",
        "http://127.0.0.1:8001/api/v1?password=secret",
        "http://127.0.0.1:8001/api/v1#secret",
    ):
        with pytest.raises(ValueError):
            benchmark.validate_target(url, allow_disposable_users=True)
    with pytest.raises(ValueError):
        benchmark.validate_target("http://127.0.0.1:8001/api/v1", False)


def test_probe_reports_nearest_rank_percentiles_and_threshold():
    report = benchmark.summarize({"login": list(range(1, 21))}, 18)
    assert report["login"] == {
        "samples": 20,
        "p50_ms": 10,
        "p95_ms": 19,
        "within_goal": True,
        "within_threshold": False,
    }


def test_probe_admin_writes_only_generated_user_and_excludes_warmup_samples(
    monkeypatch,
):
    users = []
    status_targets = []
    requests = []
    cleaned = []
    monkeypatch.setattr(
        benchmark, "cleanup_generated", lambda users, token: cleaned.extend(users)
    )

    def handle(request):
        path = request.url.path.removeprefix("/api/v1")
        body = json.loads(request.content) if request.content else {}
        requests.append((request.method, path))
        if path == "/auth/register":
            users.append({"id": f"generated-{len(users)}", **body})
            return httpx.Response(201, json=users[-1])
        if path in ("/auth/login", "/auth/refresh"):
            return httpx.Response(
                200,
                json={
                    "access_token": "secret-access",
                    "refresh_token": "secret-refresh",
                },
            )
        if path.endswith("/status"):
            status_targets.append(path.split("/")[-2])
        if path == "/auth/logout":
            return httpx.Response(204)
        return httpx.Response(200, json={})

    with httpx.Client(
        base_url="http://127.0.0.1/api/v1/", transport=httpx.MockTransport(handle)
    ) as client:
        result = benchmark.run_probe(
            client,
            samples=2,
            warmup=1,
            admin_identifier="admin-existing",
            admin_password="secret-admin-password",
        )
    assert len(users) == 3
    assert {item[1] for item in cleaned} == {user["id"] for user in users}
    assert status_targets == [item["id"] for item in users for _ in range(2)]
    assert set(result) == {
        "register",
        "login",
        "me",
        "patch_me",
        "refresh",
        "logout",
        "admin_list",
        "admin_detail",
        "admin_disable",
        "admin_enable",
    }
    assert all(len(timings) == 2 for timings in result.values())
    assert ("PATCH", "/users/me") in requests
    assert requests[-1] == ("POST", "/auth/logout")
    assert "secret" not in json.dumps(result)
    assert all(user["password"] == user["confirm_password"] for user in users)
    assert len({user["username"] for user in users}) == 3


def test_probe_does_not_expose_response_body_in_errors(monkeypatch):
    cleaned = []
    monkeypatch.setattr(
        benchmark, "cleanup_generated", lambda users, token: cleaned.extend(users)
    )
    with httpx.Client(
        base_url="http://127.0.0.1/api/v1/",
        transport=httpx.MockTransport(
            lambda _: httpx.Response(500, json={"password": "secret-password"})
        ),
    ) as client:
        with pytest.raises(RuntimeError, match="register returned HTTP 500") as error:
            benchmark.run_probe(client, samples=1, warmup=0)
    assert "secret-password" not in str(error.value)
    assert len(cleaned) == 1
    assert cleaned[0][0].startswith("probe_")
    assert cleaned[0][1] is None


def test_probe_rejects_partial_admin_configuration_before_sending_requests():
    with httpx.Client(base_url="http://127.0.0.1/api/v1/") as client:
        with pytest.raises(ValueError, match="together"):
            benchmark.run_probe(
                client, samples=1, warmup=0, admin_identifier="existing-admin"
            )


def test_cleanup_refuses_unscoped_names():
    with pytest.raises(RuntimeError, match="unexpected account name"):
        benchmark.cleanup_generated([("existing-user", str(uuid4()))], None)


@pytest.mark.parametrize("redis_failure", [False, True])
def test_cleanup_preserves_other_users_and_attempts_database_when_redis_fails(
    monkeypatch, redis_failure
):
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import Session

    from app.cache import redis as cache_module
    from app.db import session as database
    from app.db.base import Base
    from app.models.user import User

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    own_id, other_id = uuid4(), uuid4()
    own_name = "probe_" + "a" * 20
    with Session(engine) as session:
        for user_id, username in ((own_id, own_name), (other_id, "existing_user")):
            session.add(
                User(
                    id=user_id,
                    username=username,
                    email=f"{username}@example.com",
                    password_hash="irrelevant-hash",
                )
            )
        session.commit()

    class AsyncDatabase:
        async def __aenter__(self):
            self.session = Session(engine)
            return self

        async def __aexit__(self, *args):
            self.session.close()

        async def scalars(self, statement):
            return self.session.scalars(statement)

        async def execute(self, statement):
            return self.session.execute(statement)

        async def commit(self):
            self.session.commit()

    class Cache:
        records = {
            "own-session": json.dumps({"user_id": str(own_id)}),
            "other-session": json.dumps({"user_id": str(other_id)}),
        }

        async def scan_iter(self, **kwargs):
            if redis_failure:
                raise ConnectionError("sensitive connection details")
            for key in list(self.records):
                yield key

        async def get(self, key):
            return self.records[key]

        async def eval(self, script, count, key, expected):
            assert self.records[key] == expected
            self.records.pop(key)

        async def aclose(self):
            pass

    async def dispose():
        pass

    cache = Cache()
    monkeypatch.setattr(cache_module, "redis_client", cache)
    monkeypatch.setattr(database, "SessionFactory", AsyncDatabase)
    monkeypatch.setattr(database, "engine", SimpleNamespace(dispose=dispose))
    try:
        if redis_failure:
            with pytest.raises(RuntimeError, match="Cleanup failed") as error:
                benchmark.cleanup_generated([(own_name, str(own_id))], None)
            assert "sensitive" not in str(error.value)
        else:
            benchmark.cleanup_generated([(own_name, str(own_id))], None)
            assert "own-session" not in cache.records
        with Session(engine) as session:
            assert list(session.scalars(select(User.id))) == [other_id]
        assert "other-session" in cache.records
    finally:
        engine.dispose()
