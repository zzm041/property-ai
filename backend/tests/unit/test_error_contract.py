"""Unified error contract and failure-path tests."""

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field

from app.core.errors import AppError
from app.core.exception_handlers import install_exception_handlers
from app.core.request_context import RequestContextMiddleware


class ExamplePayload(BaseModel):
    count: int = Field(gt=0)
    password: str = Field(min_length=8)


@pytest.fixture
def error_client() -> Iterator[TestClient]:
    app = FastAPI()
    app.add_middleware(RequestContextMiddleware)
    install_exception_handlers(app)

    @app.post("/validate")
    def validate(_: ExamplePayload) -> dict[str, bool]:
        return {"ok": True}

    @app.get("/expected")
    def expected() -> None:
        raise AppError(
            code="SYS.EXPECTED_TEST",
            message="Expected failure",
            status_code=409,
            details={"retryable": False},
        )

    @app.get("/unexpected")
    def unexpected() -> None:
        raise RuntimeError("internal-sensitive-message")

    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


def test_app_error_uses_stable_contract(error_client: TestClient) -> None:
    response = error_client.get("/expected")

    assert response.status_code == 409
    assert response.json()["error"] == {
        "code": "SYS.EXPECTED_TEST",
        "message": "Expected failure",
        "details": {"retryable": False},
    }
    assert response.headers["x-request-id"] == response.json()["request_id"]


def test_validation_error_does_not_echo_input(error_client: TestClient) -> None:
    response = error_client.post(
        "/validate",
        json={"count": 0, "password": "secret"},
    )

    body = response.json()
    assert response.status_code == 422
    assert body["error"]["code"] == "SYS.VALIDATION_ERROR"
    assert "secret" not in response.text
    assert all(
        set(error) == {"type", "location", "message"}
        for error in body["error"]["details"]["errors"]
    )


def test_unhandled_error_hides_internal_details(error_client: TestClient) -> None:
    response = error_client.get("/unexpected")

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "SYS.INTERNAL_ERROR"
    assert "internal-sensitive-message" not in response.text
    assert response.headers["x-request-id"] == response.json()["request_id"]


def test_not_found_uses_error_contract(client: TestClient) -> None:
    response = client.get("/api/v1/does-not-exist")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "SYS.NOT_FOUND"
    assert response.headers["x-request-id"] == response.json()["request_id"]
