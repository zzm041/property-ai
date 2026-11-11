"""Unknown WeChat identity cannot silently create a User."""

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


def test_unknown_wechat_requires_bind_and_does_not_create_user(
    identity_session: Session,
) -> None:
    create_work_order_graph(identity_session)
    with TestClient(create_app(_settings())) as client:
        created = client.post(
            "/api/v1/auth/wechat/login",
            json={"code": "wx-day4-orphan"},
            headers={"X-Request-Id": "d4-wx-new"},
        )
        assert created.status_code == 401
        assert created.json()["error"]["code"] == "AUTH.WECHAT_BIND_REQUIRED"
        houses = client.get("/api/v1/me/houses", headers={"X-Request-Id": "d4-wx-houses"})
        assert houses.status_code == 401
