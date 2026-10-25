"""Run explicitly with `uv run python -m tests.integration.containers` from backend/."""

import json
import subprocess
import sys
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

from app.core.config import get_settings

BACKEND = Path(__file__).resolve().parents[2]


def compose(*args):
    result = subprocess.run(
        ["docker", "compose", "--env-file", ".env", "-f", "compose.yaml", *args],
        cwd=BACKEND,
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    return result.stdout.strip()


def check_containers():
    settings = get_settings()
    prefix = settings.api_v1_prefix.rstrip("/")
    address = compose("port", "api", "8000").splitlines()[0]
    base_url = f"http://{address}"
    request_id = "container-smoke-test"
    opener = build_opener(ProxyHandler({}))

    def request(path, expected_status):
        req = Request(f"{base_url}{path}", headers={"X-Request-ID": request_id})
        try:
            response = opener.open(req, timeout=12)
        except HTTPError as error:
            response = error
        with response:
            assert response.status == expected_status
            assert response.headers["X-Request-ID"] == request_id
            return json.load(response)

    assert request(f"{prefix}/health", 200) == {
        "status": "ok",
        "service": "FinOracle",
    }
    print("PASS: Published API port and health response")

    ready = request(f"{prefix}/ready", 200)
    assert ready["dependencies"] == {"mysql": "ok", "redis": "ok"}
    print("PASS: Container API reaches MySQL and Redis")

    error = request(f"{prefix}/container-smoke-missing", 404)
    assert error == {
        "code": "RESOURCE_NOT_FOUND",
        "message": "请求的资源不存在",
        "request_id": request_id,
    }
    print("PASS: Standard error response and request ID propagation")

    if settings.openapi_enabled:
        schema = request("/openapi.json", 200)
        assert f"{prefix}/health" in schema["paths"]
        assert f"{prefix}/ready" in schema["paths"]
        print("PASS: Container OpenAPI includes health and readiness endpoints")

    runtime_check = """
import importlib.util
import os
from pathlib import Path

from app.core.config import get_settings

assert os.geteuid() != 0, "API must run as a non-root user"
assert importlib.util.find_spec("pytest") is None, "Test dependency found in image"
for path in (".env", "tests", ".pytest_cache", ".ruff_cache"):
    assert not (Path("/app") / path).exists(), "Development file found in image"
assert not list(Path("/app/app").rglob("__pycache__"))
assert "MYSQL_ROOT_PASSWORD" not in os.environ
settings = get_settings()
assert settings.mysql_host == "mysql"
assert settings.mysql_port == 3306
assert settings.redis_host == "redis"
assert settings.redis_port == 6379
print("PASS: Non-root runtime, isolated dependencies and container hostnames")
"""
    print(compose("exec", "-T", "api", "python", "-c", runtime_check))

    image_id = compose("images", "-q", "api").splitlines()[0]
    image = subprocess.run(
        ["docker", "image", "inspect", image_id],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    image_config = json.loads(image.stdout)[0]["Config"]
    image_env = {entry.split("=", 1)[0] for entry in image_config["Env"]}
    assert "MYSQL_PASSWORD" not in image_env
    assert "MYSQL_ROOT_PASSWORD" not in image_env
    print("PASS: Database credentials are not embedded in image environment")

    assert "(head)" in compose("exec", "-T", "api", "alembic", "current")
    compose("exec", "-T", "api", "alembic", "check")
    print("PASS: Container database migration is current and matches the model")


if __name__ == "__main__":
    try:
        check_containers()
    except (AssertionError, OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"FAIL: {type(error).__name__}")
        sys.exit(1)
