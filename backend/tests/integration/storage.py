"""Exercise S3ObjectStorage against the Compose object store in a temporary bucket."""

import hashlib
import os
import secrets
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pydantic import SecretStr

from app.core.config import StorageSettings
from app.core.exceptions import AppException
from app.storage import S3ObjectStorage
from tests.integration.containers import compose


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_storage():
    address = compose("port", "minio", "9000").splitlines()[0]
    # 每次运行使用独立的桶，结束后连同对象一起删除，不触碰应用正在使用的桶
    settings = StorageSettings().model_copy(
        update={
            "s3_endpoint_url": f"http://{address}",
            "s3_bucket": f"finoracle-it-{secrets.token_hex(6)}",
        }
    )
    storage = S3ObjectStorage(settings)
    client = storage._client
    key = "documents/integration/original.pdf"

    try:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.pdf"
            # 跨越多个 1 MiB 读块，覆盖分块下载
            source.write_bytes(os.urandom(3 * 1024 * 1024 + 17))

            assert storage.stat(key) is None
            storage.put_file(key, source, content_type="application/pdf")
            client.head_bucket(Bucket=settings.s3_bucket)
            print("PASS: First upload creates the missing bucket")

            info = storage.stat(key)
            assert info is not None
            assert (info.size, info.content_type) == (
                source.stat().st_size,
                "application/pdf",
            )
            target = Path(directory) / "download.pdf"
            storage.download_file(key, target)
            assert digest(target) == digest(source)
            print("PASS: Upload, metadata and download round-trip preserve content")

            with ThreadPoolExecutor(4) as pool:
                list(pool.map(lambda _: storage.ensure_bucket(), range(4)))
            print("PASS: Concurrent bucket initialization is idempotent")

            storage.delete(key)
            storage.delete(key)
            assert storage.stat(key) is None
            missing = Path(directory) / "missing.pdf"
            try:
                storage.download_file(key, missing)
            except FileNotFoundError:
                pass
            else:
                raise AssertionError("Downloading a deleted object must fail")
            assert not missing.exists()
            print(
                "PASS: Delete is idempotent and missing objects raise FileNotFoundError"
            )

            rejected = S3ObjectStorage(
                settings.model_copy(
                    update={"s3_secret_key": SecretStr(secrets.token_hex(20))}
                )
            )
            try:
                rejected.put_file(key, source)
            except AppException as error:
                assert (error.code, error.status_code) == ("STORAGE_UNAVAILABLE", 503)
            else:
                raise AssertionError("Wrong credentials must be rejected")
            assert storage.stat(key) is None
            print("PASS: Wrong credentials return STORAGE_UNAVAILABLE without writing")
    finally:
        listing = client.list_objects_v2(Bucket=settings.s3_bucket)
        for item in listing.get("Contents", []):
            client.delete_object(Bucket=settings.s3_bucket, Key=item["Key"])
        client.delete_bucket(Bucket=settings.s3_bucket)
        print("PASS: Temporary bucket removed")


if __name__ == "__main__":
    check_storage()
