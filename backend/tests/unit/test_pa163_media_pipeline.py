"""PA-163: media that can never be scanned must say so, not look like an empty model answer.

The delivery default ``FILE_SCANNER_PROVIDER=unavailable`` fails closed: uploads
stay ``scanner_status=pending`` and every downstream media path refuses them.
Before this change the only observable symptom was an empty description
suggestion, and the 409 that blocked the attach carried ``details={}``.
"""

from hashlib import sha256
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.core.config import Settings
from app.core.errors import AppError
from app.core.readiness import check_readiness
from app.service.description_suggestion import (
    SUGGESTION_REASON_FALLBACK,
    SUGGESTION_REASONS,
    suggestion_reason,
)
from app.service.work_order import WorkOrderService


def settings(**overrides):
    return Settings(_env_file=None, app_env="test", **overrides)


# ------------------------------------------------------------- readiness surface
def test_unavailable_scanner_is_reported_as_media_that_never_becomes_usable():
    report = settings(file_scanner_provider="unavailable").media_pipeline()

    assert report["scanner"] == "unavailable"
    assert report["media_becomes_usable"] is False
    assert report["reason_code"] == "MEDIA_SCAN_UNAVAILABLE"
    assert "FILE_SCANNER_PROVIDER=clamav" in report["note"]


@pytest.mark.parametrize("provider", ["clamav"])
def test_a_configured_scanner_reports_media_as_usable(provider):
    overrides = {"file_scanner_provider": provider}
    if provider == "clamav":
        overrides["file_scanner_host"] = "127.0.0.1"
    report = settings(**overrides).media_pipeline()

    assert report["media_becomes_usable"] is True
    assert report["media_scanned"] is True
    assert report["reason_code"] is None
    assert report["note"] is None


def test_the_development_stub_is_no_longer_reported_as_usable():
    """D6 supersedes the PA-163 reading: the stub never scans, so it cannot be
    counted as a working pipeline. See test_d6_scanner_demo_mode.py."""

    report = settings(file_scanner_provider="development").media_pipeline()

    assert report["scan_mode"] == "demo_stub"
    assert report["media_scanned"] is False
    assert report["media_becomes_usable"] is False
    assert report["reason_code"] in {"MEDIA_SCAN_STUB", "MEDIA_SCAN_STUB_UNACKNOWLEDGED"}


def test_readiness_payload_carries_the_media_pipeline_report():
    payload, status = check_readiness(
        settings(file_scanner_provider="unavailable"),
        {"mysql": (True, lambda _s: None)},
        include_queue=False,
    )

    assert status == 200
    assert payload["media_pipeline"]["reason_code"] == "MEDIA_SCAN_UNAVAILABLE"
    # The overall verdict is untouched: media is not on the liveness path.
    assert payload["status"] == "ready"


# ------------------------------------------------------- reporter facing reasons
def test_every_documented_reason_code_has_a_sentence():
    for code in ("MEDIA_NOT_READY", "MEDIA_SCAN_UNAVAILABLE", "AI.UNAVAILABLE",
                 "AI.TIMEOUT", "AI.INVALID_OUTPUT", "AI.RATE_LIMITED"):
        sentence = SUGGESTION_REASONS[code]
        assert sentence and sentence.endswith("。")
    assert suggestion_reason(None) is None
    assert suggestion_reason("AI.UNHEARD_OF") == SUGGESTION_REASON_FALLBACK


# --------------------------------------------------------------- the 409 detail
def _service(**overrides):
    service = object.__new__(WorkOrderService)
    # D6: the read gate now asks the deployment whether the demo stub was
    # declared, so the fixture states it instead of relying on a status check.
    service._settings = settings(file_scanner_provider="development")
    for name, value in overrides.items():
        setattr(service, name, value)
    return service


def test_pending_media_is_refused_with_a_reason_an_operator_can_read():
    record = SimpleNamespace(scanner_status="pending", storage_key="tenant/1/file/34")

    with pytest.raises(AppError) as caught:
        _service()._read_stored_file(record)

    error = caught.value
    assert error.status_code == 409
    assert error.code == "FILE.INVALID_INPUT"
    assert error.message == "File content is not ready for use"
    assert error.details == {"reason": "MEDIA_NOT_READY", "scanner_status": "pending"}


def test_ready_media_is_read_through_the_storage_provider():
    content = bytes([0xFF, 0xD8, 0xFF, 0xD9])
    storage = MagicMock()
    storage.get_object.return_value = content
    record = SimpleNamespace(
        id=31, tenant_id=1, property_id=1,
        scanner_status="skipped_dev", storage_key="tenant/1/file/31",
        mime_type="image/jpeg", size_bytes=len(content),
        content_hash=sha256(content).hexdigest(),
    )

    assert _service(_storage=storage)._read_stored_file(record) == content
    storage.get_object.assert_called_once_with("tenant/1/file/31")


# ------------------------------------------------- the scanner actually runs once
@pytest.mark.parametrize(
    ("scan_result", "expected"),
    [("pending", "pending"), ("clean", "clean"), ("skipped_dev", "skipped_dev")],
)
def test_upload_applies_the_scanner_verdict_to_a_record_staged_as_pending(
    scan_result, expected
):
    content = bytes([0xFF, 0xD8, 0xFF, 0xD9])
    record = SimpleNamespace(
        id=34,
        tenant_id=1,
        property_id=1,
        uploader_user_id=6,
        scanner_status="pending",
        storage_key="tenant/1/file/34",
        mime_type="image/jpeg",
        size_bytes=len(content),
        content_hash="placeholder",
    )
    storage = MagicMock()
    storage.get_object.return_value = None
    scanner = MagicMock()
    scanner.scan.return_value = scan_result
    service = _service(
        _storage=storage,
        _file_scanner=scanner,
        _session=MagicMock(),
        _repository=SimpleNamespace(get_file_for_update=lambda _id: record),
        _settings=SimpleNamespace(upload_max_mb=10),
        _reload_actor=lambda actor: actor,
        _actor_can_see_file_tenant=lambda _principal, _tenant: True,
    )

    result = service._put_file_content(
        actor=SimpleNamespace(user_id=6), file_id=34, content=content,
        request_id="req-163",
    )

    assert result is record
    assert record.scanner_status == expected
    scanner.scan.assert_called_once_with(content=content, mime_type="image/jpeg")
    storage.put_object.assert_called_once_with("tenant/1/file/34", content, "image/jpeg")


def test_a_record_that_is_already_scanned_is_not_scanned_again():
    content = bytes([0xFF, 0xD8, 0xFF, 0xD9])
    record = SimpleNamespace(
        id=31, tenant_id=1, property_id=1, uploader_user_id=6,
        scanner_status="clean", storage_key="tenant/1/file/31",
        mime_type="image/jpeg", size_bytes=len(content), content_hash="placeholder",
    )
    storage = MagicMock()
    storage.get_object.return_value = None
    scanner = MagicMock()
    service = _service(
        _storage=storage, _file_scanner=scanner, _session=MagicMock(),
        _repository=SimpleNamespace(get_file_for_update=lambda _id: record),
        _settings=SimpleNamespace(upload_max_mb=10),
        _reload_actor=lambda actor: actor,
        _actor_can_see_file_tenant=lambda _principal, _tenant: True,
    )

    service._put_file_content(
        actor=SimpleNamespace(user_id=6), file_id=31, content=content,
        request_id="req-163b",
    )

    assert record.scanner_status == "clean"
    scanner.scan.assert_not_called()
