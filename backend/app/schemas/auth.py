import re
from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    SecretStr,
    StringConstraints,
    field_validator,
    model_validator,
)

from app.schemas.user import UserResponse

Username = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=3,
        max_length=32,
        pattern=r"^[A-Za-z0-9_]+$",
    ),
]


class RegisterRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        hide_input_in_errors=True,
    )

    username: Username
    email: EmailStr = Field(max_length=254)

    password: SecretStr = Field(
        min_length=8,
        max_length=128,
        exclude=True,
        repr=False,
    )
    confirm_password: SecretStr = Field(
        min_length=8,
        max_length=128,
        exclude=True,
        repr=False,
    )

    @field_validator("username")
    @classmethod
    def normalize_username(cls, value: str) -> str:
        return value.lower()

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return value.lower()

    @field_validator("password")
    @classmethod
    def validate_password_strength(cls, value: SecretStr) -> SecretStr:
        password = value.get_secret_value()

        has_letter = re.search(r"[A-Za-z]", password)
        has_digit = re.search(r"[0-9]", password)

        if not has_letter or not has_digit:
            raise ValueError("密码必须同时包含英文字母和数字")

        return value

    @model_validator(mode="after")
    def validate_password_confirmation(self) -> Self:
        if self.password.get_secret_value() != self.confirm_password.get_secret_value():
            raise ValueError("两次输入的密码不一致")

        return self


class LoginRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        hide_input_in_errors=True,
    )

    identifier: Annotated[
        str,
        StringConstraints(
            strip_whitespace=True,
            min_length=3,
            max_length=254,
        ),
    ]

    password: SecretStr = Field(
        min_length=1,
        max_length=128,
        exclude=True,
        repr=False,
    )

    @field_validator("identifier")
    @classmethod
    def normalize_identifier(cls, value: str) -> str:
        return value.lower()


class LoginResponse(BaseModel):
    model_config = ConfigDict(hide_input_in_errors=True)

    access_token: str = Field(min_length=1, repr=False)
    refresh_token: str = Field(min_length=1, repr=False)
    token_type: Literal["bearer"] = "bearer"
    expires_in: int = Field(gt=0)
    user: UserResponse


class RefreshRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    refresh_token: SecretStr = Field(
        min_length=1, max_length=4096, exclude=True, repr=False
    )
