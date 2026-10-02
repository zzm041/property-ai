"""Public delivery surfaces must not expose development or storage internals."""

from types import SimpleNamespace
from pathlib import Path

import pytest

from app.api.v1.files import _file_data, _read_upload_content
from app.core.config import Settings
from app.core.errors import AppError, FileErrorCode
from app.main import create_app


ROOT = Path(__file__).resolve().parents[3]


def _production_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "_env_file": None,
        "app_env": "production",
        "app_secret_key": "delivery-secret-longer-than-default",
        "wechat_login_enabled": False,
    }
    values.update(overrides)
    return Settings(**values)


def test_production_rejects_debug_mode() -> None:
    with pytest.raises(ValueError, match="debug"):
        _production_settings(app_debug=True)


def test_production_disables_interactive_api_documents() -> None:
    application = create_app(_production_settings())

    assert application.openapi_url is None
    assert application.docs_url is None
    assert application.redoc_url is None


def test_file_response_omits_internal_storage_and_scanner_fields() -> None:
    record = SimpleNamespace(
        id=7,
        tenant_id=2,
        property_id=3,
        uploader_user_id=5,
        mime_type="image/jpeg",
        size_bytes=128,
        storage_key="private/bucket/object-key",
        scanner_status="clean",
        created_at="2026-10-03T00:00:00Z",
        upload_url=(
            "https://storage.example/private-bucket/"
            "tenant-2/property-3/private-object?X-Amz-Signature=secret"
        ),
        upload_expires_in=300,
    )

    payload = _file_data(record).model_dump(mode="json")

    assert "storage_key" not in payload
    assert "scanner_status" not in payload
    assert "upload_url" not in payload
    assert "upload_expires_in" not in payload
    assert payload["id"] == 7
    assert payload["mime_type"] == "image/jpeg"


def test_upload_reader_rejects_oversize_without_unbounded_read() -> None:
    class BoundedProbe:
        requested_size: int | None = None

        def read(self, size: int = -1) -> bytes:
            self.requested_size = size
            return b"x" * size

    probe = BoundedProbe()

    with pytest.raises(AppError) as caught:
        _read_upload_content(probe, max_bytes=10)

    assert caught.value.code == FileErrorCode.TOO_LARGE
    assert probe.requested_size == 11


def test_public_error_messages_do_not_expose_identity_schema_or_internal_ids() -> None:
    sources = "\n".join(
        (ROOT / path).read_text(encoding="utf-8")
        for path in (
            "backend/app/service/identity_recovery.py",
            "backend/app/api/v1/admin_employee.py",
        )
    )

    for forbidden in (
        "awaits approved schema migration",
        "No approved identity proof channel is configured",
        "Identity principal loader is unavailable",
        "Direct user_id attach is not allowed",
    ):
        assert forbidden not in sources
