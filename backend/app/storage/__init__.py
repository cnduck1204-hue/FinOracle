"""对象存储：接口与对象键规则见 base，S3 API 实现见 s3。"""

from functools import lru_cache

from app.core.config import get_storage_settings
from app.storage.base import ObjectInfo, ObjectStorage, check_key
from app.storage.s3 import S3ObjectStorage


@lru_cache
def get_storage() -> ObjectStorage:
    """进程内共用一个客户端；首次调用时读取 StorageSettings。"""
    return S3ObjectStorage(get_storage_settings())


__all__ = [
    "ObjectInfo",
    "ObjectStorage",
    "S3ObjectStorage",
    "check_key",
    "get_storage",
]
