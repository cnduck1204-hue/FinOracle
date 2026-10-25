from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """FinOracle 应用配置"""

    app_name: str = "FinOracle API"
    app_env: str = "development"
    debug: bool = True
    log_level: str = "INFO"

    api_v1_prefix: str = "/api/v1"

    openapi_enabled: bool = True

    mysql_host: str = "127.0.0.1"
    mysql_port: int = 3306
    mysql_database: str = "finoracle"
    mysql_user: str = "finoracle"
    mysql_password: SecretStr

    redis_host: str = "127.0.0.1"
    redis_port: int = 6379
    redis_db: int = 0

    login_max_failures: int = Field(default=5, ge=1)
    login_lock_seconds: int = Field(default=900, ge=1)

    jwt_secret_key: SecretStr = Field(min_length=32)
    jwt_algorithm: Literal["HS256"] = "HS256"
    jwt_access_token_minutes: int = Field(default=30, ge=1)
    jwt_refresh_token_days: int = Field(default=7, ge=1)

    @field_validator("jwt_secret_key")
    @classmethod
    def validate_jwt_secret_key(cls, value: SecretStr) -> SecretStr:
        secret = value.get_secret_value()

        placeholders = (
            "change-me",
            "changeme",
            "your-secret",
            "example",
            "default",
        )

        if (
            secret != secret.strip()
            or len(set(secret)) < 8
            or any(marker in secret.lower() for marker in placeholders)
        ):
            raise ValueError("JWT 密钥配置不合格，请使用随机生成的值")

        return value

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        hide_input_in_errors=True,
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
