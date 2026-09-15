"""Request ID validation and propagation tests."""

from fastapi.testclient import TestClient


def test_safe_client_request_id_is_preserved(client: TestClient) -> None:
    response = client.get(
        "/api/v1/healthz",
        headers={"X-Request-Id": "client-request-123"},
    )

    assert response.headers["x-request-id"] == "client-request-123"
    assert response.json()["request_id"] == "client-request-123"


def test_unsafe_client_request_id_is_replaced(client: TestClient) -> None:
    response = client.get(
        "/api/v1/healthz",
        headers={"X-Request-Id": "unsafe/request/id"},
    )

    request_id = response.headers["x-request-id"]
    assert request_id != "unsafe/request/id"
    assert len(request_id) == 32
    assert response.json()["request_id"] == request_id


def test_generated_request_ids_are_unique(client: TestClient) -> None:
    first = client.get("/api/v1/healthz")
    second = client.get("/api/v1/healthz")

    assert first.headers["x-request-id"] != second.headers["x-request-id"]
