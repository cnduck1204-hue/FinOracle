from datetime import UTC, datetime, timedelta
from uuid import UUID

import jwt
from jwt.exceptions import InvalidTokenError
from pydantic import ValidationError

from app.core.config import get_settings
from app.core.exceptions import AppException
from app.schemas.token import TokenClaims, TokenType


def create_token(
    *,
    user_id: UUID,
    role: str,
    session_id: UUID,
    token_id: UUID,
    token_type: TokenType,
) -> str:
    settings = get_settings()
    now = datetime.now(UTC)

    if token_type == "access":
        lifetime = timedelta(minutes=settings.jwt_access_token_minutes)
    else:
        lifetime = timedelta(days=settings.jwt_refresh_token_days)

    claims = TokenClaims(
        sub=user_id,
        role=role,
        type=token_type,
        sid=session_id,
        jti=token_id,
        iat=int(now.timestamp()),
        exp=int((now + lifetime).timestamp()),
    )

    return jwt.encode(
        claims.model_dump(mode="json"),
        settings.jwt_secret_key.get_secret_value(),
        algorithm=settings.jwt_algorithm,
    )


def decode_token(
    token: str,
    *,
    expected_type: TokenType,
) -> TokenClaims:
    settings = get_settings()

    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key.get_secret_value(),
            algorithms=[settings.jwt_algorithm],
            options={
                "require": [
                    "sub",
                    "role",
                    "type",
                    "sid",
                    "jti",
                    "iat",
                    "exp",
                ],
            },
        )

        claims = TokenClaims.model_validate(payload)

        if claims.type != expected_type:
            raise InvalidTokenError("令牌类型不匹配")

        return claims

    except (InvalidTokenError, ValidationError) as exc:
        raise AppException(
            code="AUTH_INVALID_TOKEN",
            message="令牌无效或已过期",
            status_code=401,
        ) from exc
