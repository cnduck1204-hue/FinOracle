from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

TokenType = Literal["access", "refresh"]


class TokenClaims(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        hide_input_in_errors=True,
    )

    sub: UUID
    role: Literal["user", "admin"]
    type: TokenType
    sid: UUID
    jti: UUID
    iat: int = Field(strict=True, ge=0)
    exp: int = Field(strict=True, ge=0)
