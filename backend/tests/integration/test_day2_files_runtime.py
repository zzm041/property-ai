"""Day 2 file upload ownership and resident work-order list."""

from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import FileErrorCode, IamErrorCode
from app.main import create_app
from app.repository.identity import IdentityRepository
from app.service.auth import AuthService
from app.service.work_order import WorkOrderService
from tests.integration.conftest import commit_open_transaction
from tests.integration.work_order_test_data import create_work_order_graph

TEST_SECRET = "d2-file-secret-that-is-not-production"


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        app_env="test",
        dev_auth_enabled=True,
        app_secret_key=TEST_SECRET,
    )


def _client() -> TestClient:
    return TestClient(create_app(_settings()))


def _token(client: TestClient, user_id: int) -> str:
    response = client.post(
        "/api/v1/auth/dev/token",
        json={"user_id": user_id},
        headers={"X-Request-Id": f"d2-login-{user_id}"},
    )
    assert response.status_code == 200
    return response.json()["data"]["access_token"]


def _auth(token: str, request_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "X-Request-Id": request_id}


def test_resident_lists_own_work_orders_and_uploads_media(
    identity_session: Session,
) -> None:
    graph = create_work_order_graph(identity_session)
    with _client() as client:
        token = _token(client, graph.employees.resident_id)
        registered = client.post(
            "/api/v1/files",
            json={
                "mime_type": "image/jpeg",
                "size_bytes": 4,
                "original_name": "spark.jpg",
                "property_id": graph.employees.property_1_id,
            },
            headers=_auth(token, "d2-file-reg"),
        )
        assert registered.status_code == 200
        registered_data = registered.json()["data"]
        file_id = registered_data["id"]
        assert "storage_key" not in registered_data
        assert "scanner_status" not in registered_data
        uploaded = client.post(
            f"/api/v1/files/{file_id}/content",
            files={"file": ("spark.jpg", b"\xff\xd8\xff\xe0", "image/jpeg")},
            headers=_auth(token, "d2-file-put"),
        )
        assert uploaded.status_code == 200
        created = client.post(
            "/api/v1/work-orders",
            json={
                "house_id": graph.house_1_id,
                "text": "Kitchen outlet sparking",
                "file_ids": [file_id],
            },
            headers={
                **_auth(token, "d2-create"),
                "Idempotency-Key": f"d2-{uuid4()}",
            },
        )
        assert created.status_code == 200
        listed = client.get("/api/v1/work-orders", headers=_auth(token, "d2-list"))
        assert listed.status_code == 200
        ids = [item["id"] for item in listed.json()["data"]["items"]]
        assert created.json()["data"]["id"] in ids


def test_cross_tenant_file_content_is_not_found(identity_session: Session) -> None:
    graph = create_work_order_graph(identity_session)
    actor = AuthService(
        identity_session, IdentityRepository(identity_session)
    ).load_principal(graph.employees.resident_id)
    commit_open_transaction(identity_session)
    record = WorkOrderService(identity_session, settings=_settings()).register_file(
        actor=actor,
        mime_type="image/jpeg",
        size_bytes=4,
        request_id="d2-owner-file",
        claimed_property_id=graph.employees.property_1_id,
    )
    with _client() as client:
        other = _token(client, graph.employees.staff_t2_user_id)
        stolen = client.post(
            f"/api/v1/files/{record.id}/content",
            files={"file": ("spark.jpg", b"jpeg", "image/jpeg")},
            headers=_auth(other, "d2-file-idor"),
        )
        assert stolen.status_code == 404
        assert stolen.json()["error"]["code"] == FileErrorCode.NOT_FOUND


def test_non_uploader_same_tenant_cannot_overwrite_file(
    identity_session: Session,
) -> None:
    graph = create_work_order_graph(identity_session)
    actor = AuthService(
        identity_session, IdentityRepository(identity_session)
    ).load_principal(graph.employees.resident_id)
    commit_open_transaction(identity_session)
    record = WorkOrderService(identity_session, settings=_settings()).register_file(
        actor=actor,
        mime_type="image/jpeg",
        size_bytes=4,
        request_id="d2-staff-overwrite",
        claimed_property_id=graph.employees.property_1_id,
    )
    with _client() as client:
        staff = _token(client, graph.employees.staff_a_user_id)
        denied = client.post(
            f"/api/v1/files/{record.id}/content",
            files={"file": ("spark.jpg", b"jpeg", "image/jpeg")},
            headers=_auth(staff, "d2-file-403"),
        )
        assert denied.status_code == 403
        assert denied.json()["error"]["code"] == IamErrorCode.FORBIDDEN
