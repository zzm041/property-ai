"""Upload size and MIME rejection."""

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import FileErrorCode
from app.main import create_app
from tests.integration.work_order_test_data import create_work_order_graph

TEST_SECRET = "d4-upload-secret-that-is-not-production"


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        app_env="test",
        dev_auth_enabled=True,
        app_secret_key=TEST_SECRET,
        upload_max_mb=10,
    )


def test_upload_rejects_unsupported_type_and_oversize(
    identity_session: Session,
) -> None:
    graph = create_work_order_graph(identity_session)
    with TestClient(create_app(_settings())) as client:
        token = client.post(
            "/api/v1/auth/dev/token",
            json={"user_id": graph.employees.resident_id},
            headers={"X-Request-Id": "d4-up-login"},
        ).json()["data"]["access_token"]
        auth = {"Authorization": f"Bearer {token}"}
        unsupported = client.post(
            "/api/v1/files",
            json={
                "mime_type": "application/pdf",
                "size_bytes": 4,
                "original_name": "secret.pdf",
                "property_id": graph.employees.property_1_id,
            },
            headers={**auth, "X-Request-Id": "d4-up-pdf"},
        )
        assert unsupported.status_code == 422
        assert unsupported.json()["error"]["code"] == FileErrorCode.UNSUPPORTED_TYPE
        huge = client.post(
            "/api/v1/files",
            json={
                "mime_type": "image/jpeg",
                "size_bytes": 11 * 1024 * 1024,
                "original_name": "huge.jpg",
                "property_id": graph.employees.property_1_id,
            },
            headers={**auth, "X-Request-Id": "d4-up-huge"},
        )
        assert huge.status_code == 422
        assert huge.json()["error"]["code"] == FileErrorCode.TOO_LARGE
