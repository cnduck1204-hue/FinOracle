from typing import Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SessionRecord(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        hide_input_in_errors=True,
    )

    id: UUID
    user_id: UUID
    refresh_jti: UUID
    created_at: int = Field(strict=True, ge=0)
    expires_at: int = Field(strict=True, ge=0)
    revoked: bool = Field(default=False, strict=True)

    @model_validator(mode="after")
    def validate_lifetime(self) -> Self:
        if self.expires_at <= self.created_at:
            raise ValueError("会话过期时间必须晚于创建时间")

        return self
