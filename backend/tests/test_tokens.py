import json
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import jwt
import pytest

from app.core.config import Settings
from app.core.exceptions import AppException
from app.security import tokens

TEST_KEY = "d98f15c0e2b64aa7b91d0438f5c6e270a18b3f9d42e65c07b6a1d8e34f9205ac"


@pytest.fixture
def token_context(monkeypatch):
    settings = Settings(
        _env_file=None,
        jwt_secret_key=TEST_KEY,
        jwt_access_token_minutes=30,
        jwt_refresh_token_days=7,
    )
    monkeypatch.setattr(tokens, "get_settings", lambda: settings)
    now = int(datetime.now(UTC).timestamp())
    payload = {
        "sub": str(uuid4()),
        "sid": str(uuid4()),
        "jti": str(uuid4()),
        "role": "user",
        "type": "access",
        "iat": now - 5,
        "exp": now + 1800,
    }
    return SimpleNamespace(settings=settings, payload=payload, now=now)


def sign(context, **changes):
    return jwt.encode({**context.payload, **changes}, TEST_KEY, algorithm="HS256")


def assert_invalid(encoded, expected_type="access"):
    with pytest.raises(AppException) as caught:
        tokens.decode_token(encoded, expected_type=expected_type)

    assert (caught.value.code, caught.value.message, caught.value.status_code) == (
        "AUTH_INVALID_TOKEN",
        "令牌无效或已过期",
        401,
    )
    if encoded:
        assert encoded not in str(caught.value)


@pytest.mark.parametrize("token_type", ["access", "refresh"])
@pytest.mark.parametrize("role", ["user", "admin"])
def test_round_trip_preserves_ids_role_type_and_lifetime(
    token_context, token_type, role
):
    user_id, session_id, token_id = uuid4(), uuid4(), uuid4()
    before = int(datetime.now(UTC).timestamp())
    encoded = tokens.create_token(
        user_id=user_id,
        role=role,
        session_id=session_id,
        token_id=token_id,
        token_type=token_type,
    )
    claims = tokens.decode_token(encoded, expected_type=token_type)
    after = int(datetime.now(UTC).timestamp())

    assert (claims.sub, claims.sid, claims.jti) == (user_id, session_id, token_id)
    assert claims.role == role and claims.type == token_type
    assert before <= claims.iat <= after
    assert claims.exp - claims.iat == (1800 if token_type == "access" else 7 * 86400)
    payload = jwt.decode(encoded, TEST_KEY, algorithms=["HS256"])
    assert payload["sub"] == str(user_id)
    assert payload["sid"] == str(session_id)
    assert payload["jti"] == str(token_id)


@pytest.mark.parametrize(
    ("token_type", "seconds"), [("access", 120), ("refresh", 86400)]
)
def test_issuance_respects_configured_lifetimes(token_context, token_type, seconds):
    token_context.settings.jwt_access_token_minutes = 2
    token_context.settings.jwt_refresh_token_days = 1
    encoded = tokens.create_token(
        user_id=uuid4(),
        role="user",
        session_id=uuid4(),
        token_id=uuid4(),
        token_type=token_type,
    )
    claims = tokens.decode_token(encoded, expected_type=token_type)
    assert claims.exp - claims.iat == seconds


def test_modified_payload_is_rejected_even_when_it_remains_valid_json(token_context):
    encoded = sign(token_context)
    header, payload, signature = encoded.split(".")
    data = json.loads(jwt.utils.base64url_decode(payload))
    data["role"] = "admin"
    changed = jwt.utils.base64url_encode(json.dumps(data).encode()).decode()

    assert_invalid(f"{header}.{changed}.{signature}")


def test_modified_signature_is_rejected(token_context):
    header, payload, signature = sign(token_context).split(".")
    first = "A" if signature[0] != "A" else "B"
    assert_invalid(f"{header}.{payload}.{first}{signature[1:]}")


def test_token_signed_by_another_key_is_rejected(token_context):
    encoded = jwt.encode(token_context.payload, TEST_KEY[::-1], algorithm="HS256")
    assert_invalid(encoded)


@pytest.mark.parametrize("algorithm", ["HS384", "none"])
def test_disallowed_signing_algorithms_are_rejected(token_context, algorithm):
    key = None if algorithm == "none" else TEST_KEY
    encoded = jwt.encode(token_context.payload, key, algorithm=algorithm)
    assert_invalid(encoded)


@pytest.mark.parametrize("encoded", ["", "not-a-token", "a.b.c", "e30.e30."])
def test_malformed_tokens_are_safe_authentication_errors(token_context, encoded):
    assert_invalid(encoded)


def test_expired_tokens_are_rejected(token_context):
    assert_invalid(
        sign(token_context, iat=token_context.now - 120, exp=token_context.now - 1)
    )


def test_tokens_issued_in_the_future_are_rejected(token_context):
    assert_invalid(
        sign(token_context, iat=token_context.now + 3600, exp=token_context.now + 7200)
    )


@pytest.mark.parametrize("field", ["sub", "role", "type", "sid", "jti", "iat", "exp"])
def test_every_required_claim_must_be_present(token_context, field):
    payload = token_context.payload.copy()
    payload.pop(field)
    assert_invalid(jwt.encode(payload, TEST_KEY, algorithm="HS256"))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("sub", "invalid-uuid"),
        ("sid", "invalid-uuid"),
        ("jti", "invalid-uuid"),
        ("role", "owner"),
        ("type", "unknown"),
        ("iat", "1"),
        ("iat", 1.5),
        ("iat", True),
        ("iat", -1),
        ("iat", []),
        ("iat", {}),
        ("exp", []),
        ("exp", {}),
    ],
)
def test_invalid_claim_values_are_safe_authentication_errors(
    token_context, field, value
):
    assert_invalid(sign(token_context, **{field: value}))


@pytest.mark.parametrize("kind", ["string", "float"])
def test_expiry_requires_an_integer_even_if_library_accepts_conversion(
    token_context, kind
):
    future = token_context.now + 1800
    value = str(future) if kind == "string" else float(future) + 0.5
    assert_invalid(sign(token_context, exp=value))


@pytest.mark.parametrize(
    ("issued", "expected"), [("access", "refresh"), ("refresh", "access")]
)
def test_access_and_refresh_tokens_cannot_be_interchanged(
    token_context, issued, expected
):
    assert_invalid(sign(token_context, type=issued), expected_type=expected)


def test_unexpected_claims_are_rejected(token_context):
    assert_invalid(sign(token_context, unexpected="value"))
