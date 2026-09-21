from functools import lru_cache

from pydantic import SecretStr
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

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore"
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
