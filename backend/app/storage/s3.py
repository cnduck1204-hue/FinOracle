"""S3 API 实现：开发环境连接 Compose 中的 Silo（MinIO 维护分支），也适用于其他 S3 兼容存储。"""

import logging
import shutil
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import boto3
from botocore.client import BaseClient
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from app.core.config import StorageSettings
from app.storage.base import ObjectInfo, ObjectStorage, check_key
from app.storage.errors import storage_unavailable

logger = logging.getLogger(__name__)

_CHUNK_BYTES = 1024 * 1024
# HEAD 请求没有响应体，对象或桶不存在时只能得到状态码 404
_NOT_FOUND = frozenset({"404", "NoSuchKey", "NotFound"})
_NO_BUCKET = "NoSuchBucket"
_ALREADY_OWNED = frozenset({"BucketAlreadyOwnedByYou"})

_CLIENT_CONFIG = Config(
    signature_version="s3v4",
    # 自建存储通常没有按桶名解析的子域名
    s3={"addressing_style": "path"},
    connect_timeout=3,
    read_timeout=30,
    retries={"mode": "standard", "total_max_attempts": 3},
    # boto3 1.36 起默认为每个请求计算 CRC 校验；改为仅在接口要求时计算，兼容各 S3 实现
    request_checksum_calculation="when_required",
    response_checksum_validation="when_required",
)


class _BucketMissing(Exception):
    """桶不存在；写入时据此创建桶，读取和删除时视为对象不存在。"""


class S3ObjectStorage(ObjectStorage):
    def __init__(
        self, settings: StorageSettings, client: BaseClient | None = None
    ) -> None:
        self.bucket = settings.s3_bucket
        self._region = settings.s3_region
        # 独立 Session：boto3 默认 Session 跨线程创建客户端不安全；客户端本身可跨线程共用
        self._client = client or boto3.session.Session().client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            region_name=settings.s3_region,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key.get_secret_value(),
            config=_CLIENT_CONFIG,
        )

    def ensure_bucket(self) -> None:
        try:
            self._call(self._client.head_bucket, Bucket=self.bucket)
            return
        except (FileNotFoundError, _BucketMissing):
            pass
        options: dict[str, Any] = {}
        if self._region != "us-east-1":
            options["CreateBucketConfiguration"] = {"LocationConstraint": self._region}
        # API 与 worker 可能同时创建，桶已归自己所有即视为成功
        self._call(
            self._client.create_bucket,
            tolerate=_ALREADY_OWNED,
            Bucket=self.bucket,
            **options,
        )
        logger.info("storage_bucket_created")

    def put_file(
        self, key: str, path: Path, *, content_type: str | None = None
    ) -> None:
        check_key(key)
        extra = {"ContentType": content_type} if content_type else {}

        def upload() -> None:
            with path.open("rb") as body:
                self._call(
                    self._client.put_object,
                    Bucket=self.bucket,
                    Key=key,
                    Body=body,
                    **extra,
                )

        try:
            upload()
        except _BucketMissing:
            # 桶在首次写入时创建，存储服务是否先于 API 就绪都不影响启动
            self.ensure_bucket()
            upload()

    def download_file(self, key: str, path: Path) -> None:
        check_key(key)
        try:
            response = self._call(self._client.get_object, Bucket=self.bucket, Key=key)
        except _BucketMissing as error:
            raise FileNotFoundError(key) from error
        body = response["Body"]
        try:
            with path.open("wb") as output:
                shutil.copyfileobj(body, output, _CHUNK_BYTES)
        except BotoCoreError as error:
            path.unlink(missing_ok=True)
            logger.warning("storage_download_interrupted: %s", type(error).__name__)
            raise storage_unavailable() from error
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        finally:
            body.close()

    def open_object(self, key: str) -> tuple[ObjectInfo, Iterator[bytes]]:
        check_key(key)
        try:
            response = self._call(self._client.get_object, Bucket=self.bucket, Key=key)
        except _BucketMissing as error:
            raise FileNotFoundError(key) from error
        info = ObjectInfo(
            key=key,
            size=response["ContentLength"],
            content_type=response.get("ContentType"),
        )
        return info, _iterate(response["Body"])

    def stat(self, key: str) -> ObjectInfo | None:
        check_key(key)
        try:
            response = self._call(self._client.head_object, Bucket=self.bucket, Key=key)
        except (FileNotFoundError, _BucketMissing):
            return None
        return ObjectInfo(
            key=key,
            size=response["ContentLength"],
            content_type=response.get("ContentType"),
        )

    def delete(self, key: str) -> None:
        check_key(key)
        try:
            self._call(self._client.delete_object, Bucket=self.bucket, Key=key)
        except (FileNotFoundError, _BucketMissing):
            pass

    def delete_prefix(self, prefix: str) -> None:
        check_key(prefix.rstrip("/"))
        params: dict[str, Any] = {"Bucket": self.bucket, "Prefix": prefix}
        while True:
            try:
                page = self._call(self._client.list_objects_v2, **params)
            except (FileNotFoundError, _BucketMissing):
                return
            for item in page.get("Contents", []):
                self.delete(item["Key"])
            if not page.get("IsTruncated"):
                return
            params["ContinuationToken"] = page["NextContinuationToken"]

    def _call(
        self,
        operation: Callable[..., Any],
        /,
        *,
        tolerate: frozenset[str] = frozenset(),
        **params: Any,
    ) -> Any:
        """执行一次请求并把 S3 错误换成本模块约定的异常；日志只记录操作名和错误码。"""
        name = getattr(operation, "__name__", "request")
        try:
            return operation(**params)
        except ClientError as error:
            code = str(error.response.get("Error", {}).get("Code", ""))
            if code in tolerate:
                return None
            if code == _NO_BUCKET:
                raise _BucketMissing from error
            if code in _NOT_FOUND:
                raise FileNotFoundError(params.get("Key", self.bucket)) from error
            logger.warning("storage_request_failed: %s %s", name, code)
            raise storage_unavailable() from error
        except BotoCoreError as error:
            logger.warning("storage_unreachable: %s %s", name, type(error).__name__)
            raise storage_unavailable() from error


def _iterate(body: Any) -> Iterator[bytes]:
    try:
        yield from body.iter_chunks(_CHUNK_BYTES)
    finally:
        body.close()
