from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, field_validator


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    username: str
    email: EmailStr
    role: Literal["user", "admin"]
    status: Literal["active", "disabled"]
    last_login_at: datetime | None
    created_at: datetime
    updated_at: datetime

    @field_validator("last_login_at", "created_at", "updated_at")
    @classmethod
    def ensure_utc(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None

        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)

        return value.astimezone(UTC)
