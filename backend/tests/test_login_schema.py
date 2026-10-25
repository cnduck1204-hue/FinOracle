import pytest
from pydantic import ValidationError

from app.schemas.auth import LoginRequest


@pytest.mark.parametrize(
    ("supplied", "expected"),
    [(" Alice_01 ", "alice_01"), (" Alice@EXAMPLE.COM ", "alice@example.com")],
)
def test_login_normalizes_username_and_email(supplied, expected):
    request = LoginRequest(identifier=supplied, password="Sample123")

    assert request.identifier == expected


@pytest.mark.parametrize("identifier", ["a_1", "a" * 254])
def test_login_accepts_identifier_length_boundaries(identifier):
    request = LoginRequest(identifier=identifier, password="Sample123")

    assert request.identifier == identifier


@pytest.mark.parametrize("identifier", ["", "   ", "ab", "a" * 255])
def test_login_rejects_empty_or_out_of_range_identifier(identifier):
    with pytest.raises(ValidationError):
        LoginRequest(identifier=identifier, password="Sample123")


@pytest.mark.parametrize("password", ["x", "1" * 128, " FinOracle密码123 "])
def test_login_preserves_password_and_does_not_apply_registration_strength_rules(
    password,
):
    request = LoginRequest(identifier="alice_01", password=password)

    assert request.password.get_secret_value() == password


@pytest.mark.parametrize("password", ["", "a" * 129])
def test_login_rejects_empty_or_overlong_password(password):
    with pytest.raises(ValidationError):
        LoginRequest(identifier="alice_01", password=password)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("role", "admin"),
        ("status", "active"),
        ("password_hash", "client-hash"),
        ("confirm_password", "Sample123"),
        ("access_token", "client-token"),
    ],
)
def test_login_rejects_unexpected_fields(field, value):
    with pytest.raises(ValidationError) as caught:
        LoginRequest.model_validate(
            {"identifier": "alice_01", "password": "Sample123", field: value}
        )

    assert any(
        error["type"] == "extra_forbidden" and error["loc"] == (field,)
        for error in caught.value.errors(include_input=False)
    )


@pytest.mark.parametrize("missing", ["identifier", "password"])
def test_login_requires_both_fields(missing):
    payload = {"identifier": "alice_01", "password": "Sample123"}
    payload.pop(missing)

    with pytest.raises(ValidationError):
        LoginRequest.model_validate(payload)


def test_login_password_is_not_exposed_by_repr_or_serialization():
    password = "PrivateLoginPassword123"
    request = LoginRequest(identifier="alice_01", password=password)

    assert request.model_dump() == {"identifier": "alice_01"}
    assert password not in repr(request)
    assert password not in request.model_dump_json()


def test_login_validation_error_text_hides_password_input():
    password = "PrivateLoginPassword" * 10
    with pytest.raises(ValidationError) as caught:
        LoginRequest(identifier="alice_01", password=password)

    assert password not in str(caught.value)
    assert "input_value" not in str(caught.value)
