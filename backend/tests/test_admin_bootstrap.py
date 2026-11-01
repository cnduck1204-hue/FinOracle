import pytest
from pydantic import ValidationError

from app.core.config import Settings


def settings(**changes):
    return Settings(
        _env_file=None,
        mysql_password="test-only",
        jwt_secret_key="d98f15c0e2b64aa7b91d0438f5c6e270a18b3f9d42e65c07b6a1d8e34f9205ac",
        **changes,
    )


def test_admin_bootstrap_is_optional():
    assert settings().admin_username is None


@pytest.mark.parametrize("field", ["admin_username", "admin_email", "admin_password"])
def test_admin_bootstrap_rejects_partial_configuration(field):
    with pytest.raises(ValidationError):
        settings(**{field: "Partial123"})


def test_admin_bootstrap_normalizes_identity_and_hides_password():
    config = settings(
        admin_username=" Root_01 ",
        admin_email="Root@EXAMPLE.COM",
        admin_password="PrivateBootstrap123",
    )
    assert (config.admin_username, config.admin_email) == (
        "root_01",
        "root@example.com",
    )
    assert "PrivateBootstrap123" not in repr(config)


def test_admin_bootstrap_rejects_weak_password_without_exposing_it():
    with pytest.raises(ValidationError) as caught:
        settings(
            admin_username="root_01",
            admin_email="root@example.com",
            admin_password="weak",
        )
    assert "weak" not in str(caught.value)
