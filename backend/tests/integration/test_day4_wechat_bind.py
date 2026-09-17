"""WeChat user without a house can enter the bind-request flow."""

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.main import create_app
from tests.integration.work_order_test_data import create_work_order_graph

TEST_SECRET = "d4-wx-secret-that-is-not-production"


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        app_env="test",
        dev_auth_enabled=True,
        wechat_login_enabled=True,
        wechat_provider="development",
        app_secret_key=TEST_SECRET,
    )


def test_wechat_user_without_house_can_apply_to_bind(
    identity_session: Session,
) -> None:
    graph = create_work_order_graph(identity_session)
    with TestClient(create_app(_settings())) as client:
        created = client.post(
            "/api/v1/auth/wechat/login",
            json={"code": "wx-day4-orphan"},
            headers={"X-Request-Id": "d4-wx-new"},
        )
        assert created.status_code == 200
        token = created.json()["data"]["access_token"]
        auth = {"Authorization": f"Bearer {token}"}
        houses = client.get(
            "/api/v1/me/houses",
            headers={**auth, "X-Request-Id": "d4-wx-houses"},
        )
        assert houses.status_code == 200
        assert houses.json()["data"]["items"] == []
        bind = client.post(
            "/api/v1/resident-bind-requests",
            json={"house_id": graph.house_1_id, "relation_type": "FAMILY"},
            headers={**auth, "X-Request-Id": "d4-wx-bind"},
        )
        assert bind.status_code == 200
        assert bind.json()["data"]["status"] == "PENDING"
        assert bind.json()["data"]["house_id"] == graph.house_1_id
