from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_response_contains_request_id():
    response = client.get("/api/v1/health")

    assert response.status_code == 200

    assert "X-Request-ID" in response.headers

    assert response.headers["X-Request-ID"]


def test_existing_request_id_is_propagated():
    request_id = "test-request-123"

    response = client.get(
        "/api/v1/health",
        headers={
            "X-Request-ID": request_id,
        },
    )

    assert response.status_code == 200

    assert response.headers["X-Request-ID"] == request_id
