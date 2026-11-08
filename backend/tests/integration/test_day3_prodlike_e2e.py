"""Production-like stack smoke. Default pytest uses TestClient; live nginx is optional."""

import os

import httpx
import pytest
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.main import create_app
from tests.integration.work_order_test_data import create_work_order_graph

LIVE_BASE_URL = os.environ.get("DAY3_PRODLIKE_BASE_URL", "").rstrip("/")


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        app_env="test",
        dev_auth_enabled=True,
        wechat_login_enabled=True,
        wechat_provider="development",
        ai_enqueue_on_create=False,
        app_secret_key="day3-e2e-secret-that-is-long-enough",
    )


def _auth(token: str, request_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "X-Request-Id": request_id}


def test_stack_contract_wechat_resident_workorder_knowledge(
    identity_session: Session,
) -> None:
    graph = create_work_order_graph(identity_session)
    settings = _settings()
    with TestClient(create_app(settings)) as client:
        health = client.get("/api/v1/healthz")
        ready = client.get("/api/v1/readyz")
        ops = client.get("/api/v1/opsz")
        assert health.status_code == 200
        assert ready.status_code in {200, 503}
        assert ops.status_code == 401

        wechat = client.post(
            "/api/v1/auth/wechat/login",
            json={"code": "wx-new-day3"},
            headers={"X-Request-Id": "d3-wx"},
        )
        assert wechat.status_code == 401
        assert wechat.json()["error"]["code"] == "AUTH.WECHAT_BIND_REQUIRED"
        assert "session_key" not in str(wechat.json())

        token = client.post(
            "/api/v1/auth/dev/token",
            json={"user_id": graph.employees.resident_id},
            headers={"X-Request-Id": "d3-res-login"},
        )
        assert token.status_code == 200
        resident = token.json()["data"]["access_token"]
        resident_ops = client.get("/api/v1/opsz", headers=_auth(resident, "d3-ops-res"))
        assert resident_ops.status_code == 403
        manager_token = client.post(
            "/api/v1/auth/dev/token",
            json={"user_id": graph.employees.manager_p1_id},
            headers={"X-Request-Id": "d3-mgr-login"},
        )
        assert manager_token.status_code == 200
        manager = manager_token.json()["data"]["access_token"]
        ops = client.get("/api/v1/opsz", headers=_auth(manager, "d3-ops"))
        assert ops.status_code == 403
        system_token = client.post(
            "/api/v1/auth/dev/token",
            json={"user_id": graph.employees.system_admin_id},
            headers={"X-Request-Id": "d3-sys-login"},
        )
        assert system_token.status_code == 200
        system = system_token.json()["data"]["access_token"]
        ops = client.get("/api/v1/opsz", headers=_auth(system, "d3-ops-sys"))
        assert ops.status_code == 200
        assert ops.json()["data"]["celery_queue"] == "property_ai.test"
        houses = client.get("/api/v1/me/houses", headers=_auth(resident, "d3-houses"))
        assert houses.status_code == 200

        created = client.post(
            "/api/v1/work-orders",
            json={
                "house_id": graph.house_1_id,
                "text": "Kitchen outlet sparking",
                "category": "ELECTRICAL",
            },
            headers={
                **_auth(resident, "d3-wo-create"),
                "Idempotency-Key": f"d3-{uuid4()}",
            },
        )
        assert created.status_code == 200
        assert created.json()["data"]["id"]
        assert created.json()["data"]["status"] == "PENDING_ANALYSIS"


@pytest.mark.skipif(not LIVE_BASE_URL, reason="DAY3_PRODLIKE_BASE_URL is not set")
def test_live_nginx_health_ready_ops() -> None:
    health = httpx.get(f"{LIVE_BASE_URL}/api/v1/healthz", timeout=5.0)
    ready = httpx.get(f"{LIVE_BASE_URL}/api/v1/readyz", timeout=5.0)
    ops = httpx.get(f"{LIVE_BASE_URL}/api/v1/opsz", timeout=5.0)
    assert health.status_code == 200
    assert health.json()["data"]["status"] == "ok"
    assert ready.status_code in {200, 503}
    assert ops.status_code == 401
