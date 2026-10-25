import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.exceptions import register_exception_handlers
from app.schemas.auth import RegisterRequest


def registration_payload(**changes):
    return {
        "username": "Alice_01",
        "email": "Alice@EXAMPLE.COM",
        "password": "FinOracle123",
        "confirm_password": "FinOracle123",
        **changes,
    }


def test_username_and_email_are_normalized():
    request = RegisterRequest.model_validate(
        registration_payload(username=" Alice_01 ", email=" Alice@EXAMPLE.COM ")
    )

    assert request.username == "alice_01"
    assert request.email == "alice@example.com"


@pytest.mark.parametrize("username", ["a_1", "A" * 32])
def test_username_accepts_length_boundaries(username):
    request = RegisterRequest.model_validate(registration_payload(username=username))

    assert request.username == username.lower()


@pytest.mark.parametrize(
    "username", ["ab", "a" * 33, "中文用户", "alice-01", "alice 01", "Kelvin"]
)
def test_username_rejects_invalid_lengths_and_non_ascii_characters(username):
    with pytest.raises(ValidationError):
        RegisterRequest.model_validate(registration_payload(username=username))


@pytest.mark.parametrize("password", ["Abcdefg1", "A" * 127 + "1"])
def test_password_accepts_length_boundaries(password):
    request = RegisterRequest.model_validate(
        registration_payload(password=password, confirm_password=password)
    )

    assert request.password.get_secret_value() == password


@pytest.mark.parametrize(
    "password", ["Abcdef1", "A" * 128 + "1", "abcdefgh", "12345678", "中文密码123456"]
)
def test_password_rejects_invalid_lengths_and_missing_character_types(password):
    with pytest.raises(ValidationError):
        RegisterRequest.model_validate(
            registration_payload(password=password, confirm_password=password)
        )


def test_password_whitespace_is_preserved():
    password = " FinOracle密码123 "
    request = RegisterRequest.model_validate(
        registration_payload(password=password, confirm_password=password)
    )

    assert request.password.get_secret_value() == password
    assert request.confirm_password.get_secret_value() == password


@pytest.mark.parametrize(
    "confirmation", ["finOracle123", "FinOracle123 ", "Different123"]
)
def test_confirmation_requires_an_exact_match(confirmation):
    with pytest.raises(ValidationError):
        RegisterRequest.model_validate(
            registration_payload(confirm_password=confirmation)
        )


@pytest.mark.parametrize("field", ["username", "email", "password", "confirm_password"])
def test_all_registration_fields_are_required(field):
    payload = registration_payload()
    payload.pop(field)

    with pytest.raises(ValidationError):
        RegisterRequest.model_validate(payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("role", "admin"),
        ("role", "user"),
        ("status", "active"),
        ("password_hash", "attacker-controlled-hash"),
        ("id", "attacker-controlled-id"),
    ],
)
def test_client_cannot_supply_server_owned_fields(field, value):
    with pytest.raises(ValidationError) as caught:
        RegisterRequest.model_validate(registration_payload(**{field: value}))

    assert any(
        error["type"] == "extra_forbidden" and error["loc"] == (field,)
        for error in caught.value.errors(include_input=False)
    )


@pytest.mark.parametrize("email", ["not-an-email", "alice@", "@example.com"])
def test_email_must_be_valid(email):
    with pytest.raises(ValidationError):
        RegisterRequest.model_validate(registration_payload(email=email))


def test_passwords_are_excluded_from_repr_and_serialization():
    request = RegisterRequest.model_validate(registration_payload())

    assert request.model_dump() == {
        "username": "alice_01",
        "email": "alice@example.com",
    }
    assert "FinOracle123" not in repr(request)
    assert "FinOracle123" not in request.model_dump_json()
    assert "password" not in request.model_dump_json()


def test_validation_error_text_does_not_echo_password():
    password = "DoNotEchoThisPassword"
    with pytest.raises(ValidationError) as caught:
        RegisterRequest.model_validate(
            registration_payload(password=password, confirm_password=password)
        )

    assert password not in str(caught.value)
    assert "input_value" not in str(caught.value)


def test_invalid_registration_uses_safe_api_error_response():
    app = FastAPI()
    register_exception_handlers(app)

    @app.post("/register")
    def register(payload: RegisterRequest):
        pytest.fail("Invalid registration must never enter the endpoint")

    with TestClient(app) as client:
        response = client.post(
            "/register", json=registration_payload(role="admin", password="Secret12345")
        )

    assert response.status_code == 422
    assert response.json() == {
        "code": "REQUEST_VALIDATION_ERROR",
        "message": "请求参数不合法",
        "request_id": "unknown",
    }
    assert "Secret12345" not in response.text
    assert "FinOracle123" not in response.text
