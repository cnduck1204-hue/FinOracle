from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.schemas.user import UserResponse


class UserStatusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["active", "disabled"]


class UserPage(BaseModel):
    items: list[UserResponse]
    total: int
    page: int
    page_size: int
