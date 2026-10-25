import pytest
from pydantic import ValidationError

from app.core.config import Settings

TEST_KEY = "d98f15c0e2b64aa7b91d0438f5c6e270a18b3f9d42e65c07b6a1d8e34f9205ac"


def test_jwt_defaults_match_project_lifetimes(monkeypatch):
    for name in ["JWT_ALGORITHM", "JWT_ACCESS_TOKEN_MINUTES", "JWT_REFRESH_TOKEN_DAYS"]:
        monkeypatch.delenv(name, raising=False)
    settings = Settings(_env_file=None, jwt_secret_key=TEST_KEY)

    assert settings.jwt_algorithm == "HS256"
    assert settings.jwt_access_token_minutes == 30
    assert settings.jwt_refresh_token_days == 7


def test_jwt_settings_are_loaded_from_environment(monkeypatch):
    monkeypatch.setenv("JWT_SECRET_KEY", TEST_KEY)
    monkeypatch.setenv("JWT_ALGORITHM", "HS256")
    monkeypatch.setenv("JWT_ACCESS_TOKEN_MINUTES", "15")
    monkeypatch.setenv("JWT_REFRESH_TOKEN_DAYS", "3")
    settings = Settings(_env_file=None)

    assert settings.jwt_secret_key.get_secret_value() == TEST_KEY
    assert settings.jwt_algorithm == "HS256"
    assert settings.jwt_access_token_minutes == 15
    assert settings.jwt_refresh_token_days == 3
    assert TEST_KEY not in repr(settings)
    assert TEST_KEY not in settings.model_dump_json()


def test_jwt_secret_has_no_implicit_default(monkeypatch):
    monkeypatch.delenv("JWT_SECRET_KEY", raising=False)

    with pytest.raises(ValidationError) as caught:
        Settings(_env_file=None)

    assert any(
        error["type"] == "missing" and error["loc"] == ("jwt_secret_key",)
        for error in caught.value.errors(include_input=False)
    )


@pytest.mark.parametrize(
    "secret",
    [
        "",
        TEST_KEY[:31],
        "a" * 64,
        "change-me-" + TEST_KEY,
        "CHANGEme-" + TEST_KEY,
        "prefix-your-secret-" + TEST_KEY,
        "prefix-example-" + TEST_KEY,
        "prefix-DEFAULT-" + TEST_KEY,
        " " + TEST_KEY,
        TEST_KEY + " ",
    ],
)
def test_obviously_invalid_jwt_secrets_are_rejected_without_echoing_input(secret):
    with pytest.raises(ValidationError) as caught:
        Settings(_env_file=None, jwt_secret_key=secret)

    message = str(caught.value)
    assert "input_value" not in message
    if secret:
        assert secret not in message


@pytest.mark.parametrize("environment", ["development", "production"])
def test_weak_secret_is_rejected_in_every_environment(environment):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, app_env=environment, jwt_secret_key="a" * 64)


@pytest.mark.parametrize("algorithm", ["none", "HS384", "RS256"])
def test_jwt_algorithm_is_restricted_to_hs256(algorithm):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, jwt_secret_key=TEST_KEY, jwt_algorithm=algorithm)


@pytest.mark.parametrize(
    "field", ["jwt_access_token_minutes", "jwt_refresh_token_days"]
)
@pytest.mark.parametrize("value", [0, -1])
def test_token_lifetimes_must_be_positive(field, value):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, jwt_secret_key=TEST_KEY, **{field: value})
