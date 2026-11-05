import pytest
from pydantic import ValidationError

from app.core.config import ParsingSettings, Settings


def test_settings_default_values():
    settings = Settings(_env_file=None)

    assert settings.app_name == "FinOracle API"
    assert settings.app_env == "development"
    assert settings.debug is True
    assert settings.log_level == "INFO"
    assert settings.api_v1_prefix == "/api/v1"
    assert settings.openapi_enabled is True


def test_settings_can_be_overridden_by_environment(monkeypatch):
    monkeypatch.setenv("APP_NAME", "FinOracle Test API")
    monkeypatch.setenv("DEBUG", "false")
    monkeypatch.setenv("API_V1_PREFIX", "/test/api")
    monkeypatch.setenv("OPENAPI_ENABLED", "false")

    settings = Settings(_env_file=None)

    assert settings.app_name == "FinOracle Test API"
    assert settings.debug is False
    assert settings.api_v1_prefix == "/test/api"
    assert settings.openapi_enabled is False


def test_redis_settings_can_be_overridden_by_environment(monkeypatch):
    monkeypatch.setenv("REDIS_HOST", "redis")
    monkeypatch.setenv("REDIS_PORT", "6380")
    monkeypatch.setenv("REDIS_DB", "2")

    settings = Settings(_env_file=None)

    assert settings.redis_host == "redis"
    assert settings.redis_port == 6380
    assert settings.redis_db == 2


def test_parsing_settings_load_without_application_secrets(monkeypatch):
    monkeypatch.delenv("MYSQL_PASSWORD", raising=False)
    monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
    monkeypatch.setenv("SOFFICE_PATH", "  D:/LibreOffice/program/soffice.exe ")

    settings = ParsingSettings(_env_file=None)

    assert settings.soffice_path == "D:/LibreOffice/program/soffice.exe"


def test_blank_conversion_settings_mean_unset(monkeypatch):
    monkeypatch.setenv("SOFFICE_PATH", " ")
    monkeypatch.setenv("GOTENBERG_URL", "")
    settings = ParsingSettings(_env_file=None)
    assert (settings.soffice_path, settings.gotenberg_url) == (None, None)
    assert settings.conversion_timeout_seconds == 120


def test_conversion_timeout_must_be_positive(monkeypatch):
    monkeypatch.setenv("CONVERSION_TIMEOUT_SECONDS", "0")
    with pytest.raises(ValidationError):
        ParsingSettings(_env_file=None)
