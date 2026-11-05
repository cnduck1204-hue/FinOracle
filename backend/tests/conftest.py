import os

from app.core.config import ParsingSettings, Settings, StorageSettings

os.environ.setdefault("MYSQL_PASSWORD", "test-password")
os.environ.setdefault(
    "JWT_SECRET_KEY",
    "d98f15c0e2b64aa7b91d0438f5c6e270a18b3f9d42e65c07b6a1d8e34f9205ac",
)

# 单元测试不读取本机 backend/.env，避免管理员账号、端口等本地配置影响结果
for settings_class in (Settings, ParsingSettings, StorageSettings):
    settings_class.model_config["env_file"] = None
