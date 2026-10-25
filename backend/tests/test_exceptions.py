from fastapi.testclient import TestClient

from app.core.exceptions import AppException
from app.main import app

client = TestClient(app)


def test_not_found_uses_standard_error_response():
    response = client.get("/api/v1/not-exists")

    assert response.status_code == 404

    body = response.json()

    assert body["code"] == "RESOURCE_NOT_FOUND"
    assert body["message"] == "请求的资源不存在"

    assert "request_id" in body

    assert response.headers["X-Request-ID"] == body["request_id"]


def test_app_exception_contains_business_information():
    exc = AppException(
        code="USER_USERNAME_EXISTS",
        message="用户名已存在",
        status_code=409,
    )

    assert exc.code == "USER_USERNAME_EXISTS"
    assert exc.message == "用户名已存在"
    assert exc.status_code == 409
