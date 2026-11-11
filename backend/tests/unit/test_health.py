"""Health and Critical/Degraded readiness contract tests."""

from typing import Any

from fastapi.testclient import TestClient

from app.api.v1 import health


def _readiness(
    *,
    mysql: str = "up",
    redis: str = "up",
    rabbitmq: str = "up",
) -> tuple[dict[str, Any], int]:
    dependencies = {
        "mysql": {"status": mysql, "critical": True, "latency_ms": 1.0},
        "redis": {"status": redis, "critical": False, "latency_ms": 1.0},
        "rabbitmq": {"status": rabbitmq, "critical": False, "latency_ms": 1.0},
    }
    if mysql == "down":
        return {"status": "not_ready", "dependencies": dependencies}, 503
    if redis == "down" or rabbitmq == "down":
        return {"status": "degraded", "dependencies": dependencies}, 200
    return {"status": "ready", "dependencies": dependencies}, 200


def test_healthz_is_liveness_only(client: TestClient) -> None:
    response = client.get("/api/v1/healthz")

    assert response.status_code == 200
    assert response.json()["data"] == {"status": "ok"}
    assert response.headers["x-request-id"] == response.json()["request_id"]


def test_readyz_is_ready_when_all_dependencies_are_up(
    client: TestClient, monkeypatch: Any
) -> None:
    monkeypatch.setattr(health, "check_readiness", lambda _: _readiness())

    response = client.get("/api/v1/readyz")

    assert response.status_code == 200
    assert response.json()["data"]["status"] == "ready"


def test_readyz_is_degraded_when_redis_is_down(
    client: TestClient, monkeypatch: Any
) -> None:
    monkeypatch.setattr(
        health,
        "check_readiness",
        lambda _: _readiness(redis="down"),
    )

    response = client.get("/api/v1/readyz")

    assert response.status_code == 200
    assert response.json()["data"]["status"] == "degraded"
    assert response.json()["data"]["dependencies"]["mysql"]["status"] == "up"


def test_readyz_is_degraded_when_rabbitmq_is_down(
    client: TestClient, monkeypatch: Any
) -> None:
    monkeypatch.setattr(
        health,
        "check_readiness",
        lambda _: _readiness(rabbitmq="down"),
    )

    response = client.get("/api/v1/readyz")

    assert response.status_code == 200
    assert response.json()["data"]["status"] == "degraded"


def test_readyz_is_unavailable_when_mysql_is_down(
    client: TestClient, monkeypatch: Any
) -> None:
    monkeypatch.setattr(
        health,
        "check_readiness",
        lambda _: _readiness(mysql="down"),
    )

    response = client.get("/api/v1/readyz")

    assert response.status_code == 503
    assert response.json()["data"]["status"] == "not_ready"
