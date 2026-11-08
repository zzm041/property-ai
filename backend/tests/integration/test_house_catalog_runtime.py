"""House catalog HTTP contract tests."""

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app


def test_catalog_requires_authentication() -> None:
    settings = Settings(
        _env_file=None,
        app_env="test",
        dev_auth_enabled=False,
        app_secret_key="catalog-test-secret-that-is-not-production",
    )
    with TestClient(create_app(settings)) as client:
        response = client.get("/api/v1/properties")
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "AUTH.MISSING_TOKEN"
