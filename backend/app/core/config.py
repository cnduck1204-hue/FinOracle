from functools import lru_cache
from typing import Literal, Self

from pydantic import Field, SecretStr, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.schemas.auth import RegisterRequest

# 各配置类共用：从 backend/.env 与环境变量读取，变量名不区分大小写
_ENV_SOURCE = SettingsConfigDict(
    env_file=".env",
    env_file_encoding="utf-8",
    case_sensitive=False,
    extra="ignore",
    hide_input_in_errors=True,
)


class DatabaseSettings(BaseSettings):
    """MySQL 连接配置。worker 只加载这部分与其他所需配置，不接触 JWT 等认证密钥。"""

    mysql_host: str = "127.0.0.1"
    mysql_port: int = 3306
    mysql_database: str = "finoracle"
    mysql_user: str = "finoracle"
    mysql_password: SecretStr

    model_config = _ENV_SOURCE


class Settings(DatabaseSettings):
    """FinOracle 应用配置"""

    app_name: str = "FinOracle API"
    app_env: str = "development"
    debug: bool = True
    log_level: str = "INFO"

    api_v1_prefix: str = "/api/v1"

    openapi_enabled: bool = True

    redis_host: str = "127.0.0.1"
    redis_port: int = 6379
    redis_db: int = 0

    login_max_failures: int = Field(default=5, ge=1)
    login_lock_seconds: int = Field(default=900, ge=1)

    jwt_secret_key: SecretStr = Field(min_length=32)
    jwt_algorithm: Literal["HS256"] = "HS256"
    jwt_access_token_minutes: int = Field(default=30, ge=1)
    jwt_refresh_token_days: int = Field(default=7, ge=1)

    admin_username: str | None = None
    admin_email: str | None = None
    admin_password: SecretStr | None = Field(default=None, repr=False)

    @model_validator(mode="after")
    def validate_admin_configuration(self) -> Self:
        self.admin_username = self.admin_username or None
        self.admin_email = self.admin_email or None
        if (
            self.admin_password is not None
            and not self.admin_password.get_secret_value()
        ):
            self.admin_password = None
        values = (self.admin_username, self.admin_email, self.admin_password)
        if not any(value is not None for value in values):
            return self
        if any(value is None for value in values):
            raise ValueError("管理员初始化配置必须同时提供用户名、邮箱和密码")
        try:
            payload = RegisterRequest(
                username=self.admin_username,
                email=self.admin_email,
                password=self.admin_password,
                confirm_password=self.admin_password,
            )
        except ValidationError as exc:
            raise ValueError("管理员初始化配置格式不合法") from exc
        self.admin_username = payload.username
        self.admin_email = str(payload.email)
        return self

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

    model_config = _ENV_SOURCE


class ParsingSettings(BaseSettings):
    """文档解析中与部署机器相关的配置；不含密钥，命令行无需数据库配置即可加载。

    解析算法参数见 app.rag.parsing.options，不从环境变量读取。
    """

    # Gotenberg 地址，如 http://gotenberg:3000；配置后 .doc 等格式交给它转换为 PDF
    gotenberg_url: str | None = None
    # 未配置 Gotenberg 时使用本机 LibreOffice；为空时从 PATH 查找 soffice
    soffice_path: str | None = None
    # 单个文档格式转换的超时
    conversion_timeout_seconds: float = Field(default=120, gt=0)

    @field_validator("gotenberg_url", "soffice_path")
    @classmethod
    def blank_as_none(cls, value: str | None) -> str | None:
        # .env 中留空的配置项视为未配置
        return (value or "").strip() or None

    model_config = _ENV_SOURCE


class StorageSettings(BaseSettings):
    """对象存储配置：通过 S3 API 访问，开发环境由 Compose 运行 MinIO。

    单独成类，worker 与导入脚本只需这部分配置即可读写原件。
    """

    # API 容器内为 http://minio:9000，本机 uvicorn 或脚本为 http://127.0.0.1:9000
    s3_endpoint_url: str = "http://127.0.0.1:9000"
    s3_region: str = "us-east-1"
    s3_access_key: str = Field(default="finoracle", min_length=3)
    s3_secret_key: SecretStr = Field(min_length=8, repr=False)
    s3_bucket: str = Field(
        default="finoracle-documents",
        # S3 桶名规则：3–63 位小写字母、数字、连字符，首尾为字母或数字
        pattern=r"^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$",
    )

    @field_validator("s3_endpoint_url")
    @classmethod
    def validate_endpoint(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        if not value.startswith(("http://", "https://")):
            raise ValueError("S3_ENDPOINT_URL 必须以 http:// 或 https:// 开头")
        return value

    model_config = _ENV_SOURCE


class WorkerSettings(DatabaseSettings):
    """worker 进程配置：数据库连接与任务调度参数。"""

    log_level: str = "INFO"
    # 没有任务时的轮询间隔
    worker_poll_seconds: float = Field(default=2, gt=0)
    # 任务租约；worker 每 5 秒续租一次，进程退出后租约到期，任务由其他 worker 接手
    job_lease_seconds: int = Field(default=60, ge=20)
    # 解析完成且没有待 OCR 图片的版本自动批准并向量化；关闭后须管理员调用批准接口
    auto_approve_parsed: bool = True


_EMBEDDING_DIMENSIONS = frozenset({64, 128, 256, 512, 768, 1024, 1536, 2048})


class EmbeddingSettings(BaseSettings):
    """向量化服务：DashScope 的 OpenAI 兼容接口。离线入库与在线查询必须使用同一模型和维度。"""

    # 北京地域；国际站为 https://dashscope-intl.aliyuncs.com/compatible-mode/v1
    embedding_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    embedding_model: str = Field(default="text-embedding-v4", pattern=r"^[\w.-]+$")
    # text-embedding-v4 支持 64、128、256、512、768、1024、1536、2048
    embedding_dimension: int = 1024
    # text-embedding-v4 每次最多 10 条，超过返回 400
    embedding_batch_size: int = Field(default=10, ge=1, le=10)
    embedding_timeout_seconds: float = Field(default=30, gt=0)
    dashscope_api_key: SecretStr | None = Field(default=None, repr=False)

    @field_validator("embedding_base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        if not value.startswith(("http://", "https://")):
            raise ValueError("EMBEDDING_BASE_URL 必须以 http:// 或 https:// 开头")
        return value

    @field_validator("embedding_dimension")
    @classmethod
    def validate_dimension(cls, value: int) -> int:
        if value not in _EMBEDDING_DIMENSIONS:
            raise ValueError(
                f"EMBEDDING_DIMENSION 须为 {sorted(_EMBEDDING_DIMENSIONS)} 之一"
            )
        return value

    @field_validator("dashscope_api_key")
    @classmethod
    def blank_key_as_none(cls, value: SecretStr | None) -> SecretStr | None:
        return value if value and value.get_secret_value().strip() else None

    model_config = _ENV_SOURCE


class VectorStoreSettings(BaseSettings):
    """Milvus 向量库。集合名由前缀、模型与维度组成，换模型时写入新集合。"""

    milvus_uri: str = "http://127.0.0.1:19530"
    milvus_token: SecretStr | None = Field(default=None, repr=False)
    milvus_collection_prefix: str = Field(
        default="finoracle_chunks", pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,100}$"
    )
    milvus_timeout_seconds: float = Field(default=30, gt=0)

    @field_validator("milvus_token")
    @classmethod
    def blank_token_as_none(cls, value: SecretStr | None) -> SecretStr | None:
        return value if value and value.get_secret_value().strip() else None

    model_config = _ENV_SOURCE


@lru_cache
def get_settings() -> Settings:
    return Settings()


@lru_cache
def get_parsing_settings() -> ParsingSettings:
    return ParsingSettings()


@lru_cache
def get_storage_settings() -> StorageSettings:
    return StorageSettings()


@lru_cache
def get_embedding_settings() -> EmbeddingSettings:
    return EmbeddingSettings()


@lru_cache
def get_vector_store_settings() -> VectorStoreSettings:
    return VectorStoreSettings()
