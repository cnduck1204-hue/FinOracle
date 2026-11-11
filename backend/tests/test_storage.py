import io
import logging

import pytest
from botocore.exceptions import EndpointConnectionError, ReadTimeoutError
from botocore.response import StreamingBody
from botocore.stub import ANY, Stubber

import app.storage as storage_module
from app.core.config import StorageSettings
from app.core.exceptions import AppException
from app.storage import ObjectInfo, S3ObjectStorage, check_key, get_storage

BUCKET = "test-bucket"
SECRET = "unit-test-secret-value"


def settings(**overrides) -> StorageSettings:
    values = {"s3_secret_key": SECRET, "s3_bucket": BUCKET, **overrides}
    return StorageSettings(_env_file=None, **values)


@pytest.fixture(scope="module")
def shared_storage() -> S3ObjectStorage:
    # 创建 boto3 客户端约需 1 秒；各用例共用一个客户端，每次换新的 Stubber
    return S3ObjectStorage(settings())


@pytest.fixture
def stubbed(shared_storage):
    with Stubber(shared_storage._client) as stubber:
        yield shared_storage, stubber
        stubber.assert_no_pending_responses()


def body(data: bytes) -> StreamingBody:
    return StreamingBody(io.BytesIO(data), len(data))


def test_put_file_uploads_file_with_content_type(stubbed, tmp_path):
    storage, stubber = stubbed
    path = tmp_path / "original.pdf"
    path.write_bytes(b"%PDF-1.7")
    stubber.add_response(
        "put_object",
        {},
        {
            "Bucket": BUCKET,
            "Key": "a/b/original.pdf",
            "Body": ANY,
            "ContentType": "application/pdf",
        },
    )
    storage.put_file("a/b/original.pdf", path, content_type="application/pdf")


def test_put_file_creates_missing_bucket_and_retries_once(stubbed, tmp_path):
    storage, stubber = stubbed
    path = tmp_path / "file.txt"
    path.write_text("内容", encoding="utf-8")
    upload = {"Bucket": BUCKET, "Key": "k/file.txt", "Body": ANY}
    stubber.add_client_error(
        "put_object", "NoSuchBucket", http_status_code=404, expected_params=upload
    )
    stubber.add_client_error(
        "head_bucket", "404", http_status_code=404, expected_params={"Bucket": BUCKET}
    )
    stubber.add_response("create_bucket", {}, {"Bucket": BUCKET})
    stubber.add_response("put_object", {}, upload)
    storage.put_file("k/file.txt", path)


def test_ensure_bucket_keeps_existing_bucket(stubbed):
    storage, stubber = stubbed
    stubber.add_response("head_bucket", {}, {"Bucket": BUCKET})
    storage.ensure_bucket()


def test_ensure_bucket_accepts_bucket_created_concurrently(stubbed):
    storage, stubber = stubbed
    stubber.add_client_error("head_bucket", "404", http_status_code=404)
    stubber.add_client_error(
        "create_bucket", "BucketAlreadyOwnedByYou", http_status_code=409
    )
    storage.ensure_bucket()


def test_ensure_bucket_passes_region_outside_us_east_1():
    storage = S3ObjectStorage(settings(s3_region="cn-north-1"))
    with Stubber(storage._client) as stubber:
        stubber.add_client_error("head_bucket", "404", http_status_code=404)
        stubber.add_response(
            "create_bucket",
            {},
            {
                "Bucket": BUCKET,
                "CreateBucketConfiguration": {"LocationConstraint": "cn-north-1"},
            },
        )
        storage.ensure_bucket()
        stubber.assert_no_pending_responses()


def test_bucket_owned_by_another_account_is_a_configuration_error(stubbed):
    storage, stubber = stubbed
    stubber.add_client_error("head_bucket", "404", http_status_code=404)
    stubber.add_client_error(
        "create_bucket", "BucketAlreadyExists", http_status_code=409
    )
    with pytest.raises(AppException) as caught:
        storage.ensure_bucket()
    assert (caught.value.code, caught.value.status_code) == ("STORAGE_UNAVAILABLE", 503)


def test_stat_returns_object_information(stubbed):
    storage, stubber = stubbed
    stubber.add_response(
        "head_object",
        {"ContentLength": 8, "ContentType": "application/pdf"},
        {"Bucket": BUCKET, "Key": "x/original.pdf"},
    )
    assert storage.stat("x/original.pdf") == ObjectInfo(
        key="x/original.pdf", size=8, content_type="application/pdf"
    )


@pytest.mark.parametrize("code", ["404", "NoSuchBucket"])
def test_stat_returns_none_for_missing_object_or_bucket(stubbed, code):
    storage, stubber = stubbed
    stubber.add_client_error("head_object", code, http_status_code=404)
    assert storage.stat("x/missing.pdf") is None


def test_download_file_writes_object_content(stubbed, tmp_path):
    storage, stubber = stubbed
    data = b"0123456789" * 300_000
    stubber.add_response(
        "get_object",
        {"Body": body(data), "ContentLength": len(data)},
        {"Bucket": BUCKET, "Key": "x/original.pdf"},
    )
    target = tmp_path / "download.pdf"
    storage.download_file("x/original.pdf", target)
    assert target.read_bytes() == data


@pytest.mark.parametrize("code", ["NoSuchKey", "NoSuchBucket"])
def test_download_missing_object_raises_file_not_found(stubbed, tmp_path, code):
    storage, stubber = stubbed
    stubber.add_client_error("get_object", code, http_status_code=404)
    target = tmp_path / "download.pdf"
    with pytest.raises(FileNotFoundError):
        storage.download_file("x/missing.pdf", target)
    assert not target.exists()


def test_interrupted_download_leaves_no_partial_file(stubbed, tmp_path):
    storage, stubber = stubbed

    class BrokenStream:
        reads = 0

        def read(self, amount=None):
            self.reads += 1
            if self.reads > 1:
                raise ReadTimeoutError(endpoint_url="http://storage")
            return b"partial"

        def close(self):
            pass

    stubber.add_response("get_object", {"Body": BrokenStream()})
    target = tmp_path / "download.pdf"
    with pytest.raises(AppException) as caught:
        storage.download_file("x/original.pdf", target)
    assert caught.value.code == "STORAGE_UNAVAILABLE"
    assert not target.exists()


@pytest.mark.parametrize("code", [None, "NoSuchBucket"])
def test_delete_is_idempotent(stubbed, code):
    storage, stubber = stubbed
    params = {"Bucket": BUCKET, "Key": "x/original.pdf"}
    if code is None:
        stubber.add_response("delete_object", {}, params)
    else:
        stubber.add_client_error(
            "delete_object", code, http_status_code=404, expected_params=params
        )
    storage.delete("x/original.pdf")


def test_rejected_credentials_become_storage_unavailable_without_leaking(
    stubbed, tmp_path, caplog
):
    storage, stubber = stubbed
    path = tmp_path / "file.txt"
    path.write_text("x")
    stubber.add_client_error(
        "put_object", "SignatureDoesNotMatch", http_status_code=403
    )
    with caplog.at_level(logging.WARNING), pytest.raises(AppException) as caught:
        storage.put_file("k/file.txt", path)
    assert (caught.value.code, caught.value.status_code) == ("STORAGE_UNAVAILABLE", 503)
    assert "SignatureDoesNotMatch" in caplog.text
    assert SECRET not in caplog.text


def test_unreachable_storage_becomes_storage_unavailable(monkeypatch, shared_storage):
    def unreachable(**params):
        raise EndpointConnectionError(endpoint_url="http://127.0.0.1:9000")

    monkeypatch.setattr(shared_storage._client, "head_object", unreachable)
    with pytest.raises(AppException) as caught:
        shared_storage.stat("x/original.pdf")
    assert caught.value.code == "STORAGE_UNAVAILABLE"


@pytest.mark.parametrize(
    "key", ["", "/abs/key", "a//b", "a/../b", "./a", "a/", "a\\b", ".."]
)
def test_invalid_object_keys_are_rejected_before_any_request(stubbed, key):
    storage, _ = stubbed
    with pytest.raises(ValueError):
        storage.stat(key)


def test_valid_object_keys_are_accepted():
    key = "documents/0f4c/1a2b/original.pdf"
    assert check_key(key) == key


def test_get_storage_reuses_one_client(monkeypatch):
    monkeypatch.setattr(storage_module, "get_storage_settings", settings)
    get_storage.cache_clear()
    try:
        storage = get_storage()
        assert get_storage() is storage
        assert storage.bucket == BUCKET
    finally:
        get_storage.cache_clear()


def test_open_object_streams_content_and_closes_the_body(stubbed):
    storage, stubber = stubbed
    data = b"%PDF-1.7 " + b"x" * 2_500_000
    stream = body(data)
    stubber.add_response(
        "get_object",
        {"Body": stream, "ContentLength": len(data), "ContentType": "application/pdf"},
        {"Bucket": BUCKET, "Key": "x/original.pdf"},
    )

    info, chunks = storage.open_object("x/original.pdf")

    assert info == ObjectInfo("x/original.pdf", len(data), "application/pdf")
    received = list(chunks)
    assert b"".join(received) == data
    assert len(received) == 3
    assert stream._raw_stream.closed


@pytest.mark.parametrize("code", ["NoSuchKey", "NoSuchBucket"])
def test_open_missing_object_fails_before_streaming(stubbed, code):
    storage, stubber = stubbed
    stubber.add_client_error("get_object", code, http_status_code=404)
    with pytest.raises(FileNotFoundError):
        storage.open_object("x/missing.pdf")


def test_delete_prefix_walks_every_page(stubbed):
    storage, stubber = stubbed
    stubber.add_response(
        "list_objects_v2",
        {
            "Contents": [{"Key": "a/pages/p1.png"}],
            "IsTruncated": True,
            "NextContinuationToken": "next",
        },
        {"Bucket": BUCKET, "Prefix": "a/pages/"},
    )
    stubber.add_response(
        "delete_object", {}, {"Bucket": BUCKET, "Key": "a/pages/p1.png"}
    )
    stubber.add_response(
        "list_objects_v2",
        {"IsTruncated": False},
        {"Bucket": BUCKET, "Prefix": "a/pages/", "ContinuationToken": "next"},
    )
    storage.delete_prefix("a/pages/")
    stubber.assert_no_pending_responses()


def test_delete_prefix_ignores_a_missing_bucket(stubbed):
    storage, stubber = stubbed
    stubber.add_client_error("list_objects_v2", "NoSuchBucket", http_status_code=404)
    storage.delete_prefix("a/pages/")
