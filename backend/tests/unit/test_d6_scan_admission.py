"""D6: unverified media never enters a scanner-dependent path silently.

`scanner_status=skipped_dev` means "no scanner read these bytes". Before this,
both the work-order media gate and the description suggestion compared the
status against a local {"clean", "skipped_dev"} set, so an unacknowledged
`development` stub pushed unexamined bytes into VLM/ASR analysis while
readiness claimed `media_becomes_usable=False`.

The matrix below pins the three deployment states the review asked for -
a real verdict (`available`), the demo stub (`skipped_dev`), and no scanner at
all (`unavailable`) - and asserts which branch each one takes.
"""

from __future__ import annotations

from hashlib import sha256
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.core.config import Settings
from app.core.errors import AppError
from app.service.work_order.description_suggestion import (
    DescriptionSuggestion,
    DescriptionSuggestionService,
    suggestion_reason,
)
from app.service.work_order.media_scan import (
    REASON_BLOCKED,
    REASON_DEMO_STUB,
    REASON_NOT_READY,
    REASON_UNKNOWN,
    admit_scanned_media,
)
from app.service.work_order import WorkOrderService


JPEG = b"\xff\xd8\xff\xe0synthetic"


def settings(*, provider: str = "development", app_env: str = "staging", acknowledged: bool = False) -> Settings:
    extra = {"file_scanner_host": "10.0.0.5"} if provider == "clamav" else {}
    return Settings(
        _env_file=None,
        app_env=app_env,
        file_scanner_provider=provider,
        file_scanner_demo_acknowledged=acknowledged,
        **extra,
    )


def scan_events(caplog) -> list[dict]:
    """structlog keeps the event dict as the record's payload, not its message."""

    payloads = (item.msg for item in caplog.records if isinstance(item.msg, dict))
    return [payload for payload in payloads if payload.get("event") == "media_scan_skipped"]


def record(*, status: str = "skipped_dev") -> SimpleNamespace:
    return SimpleNamespace(
        id=31,
        tenant_id=1,
        property_id=1,
        uploader_user_id=6,
        scanner_status=status,
        storage_key="tenant/1/file/31",
        mime_type="image/jpeg",
        size_bytes=len(JPEG),
        content_hash=sha256(JPEG).hexdigest(),
    )


# --------------------------------------------------------------- the rule itself
def test_a_real_verdict_is_usable_without_any_explanation():
    admission = admit_scanned_media("clean", settings(provider="clamav"))
    assert admission.usable is True
    assert admission.reason_code is None
    assert admission.scan_mode == "real"
    assert admission.demo_stub_skip is False


def test_the_stub_is_usable_once_the_deployment_declares_demo_mode():
    admission = admit_scanned_media(
        "skipped_dev", settings(provider="development", acknowledged=True)
    )
    assert admission.usable is True
    assert admission.reason_code == REASON_DEMO_STUB
    assert admission.scan_mode == "demo_stub"
    # Usable, but the caller still has to record the skip.
    assert admission.demo_stub_skip is True


def test_an_unacknowledged_stub_refuses_the_media():
    admission = admit_scanned_media(
        "skipped_dev", settings(provider="development", acknowledged=False)
    )
    assert admission.usable is False
    assert admission.reason_code == "MEDIA_SCAN_STUB_UNACKNOWLEDGED"
    assert admission.demo_stub_skip is False


def test_no_scanner_refuses_even_a_file_uploaded_during_demo_mode():
    admission = admit_scanned_media(
        "skipped_dev", settings(provider="unavailable", acknowledged=False)
    )
    assert admission.usable is False
    assert admission.reason_code == "MEDIA_SCAN_UNAVAILABLE"


@pytest.mark.parametrize(
    ("status", "reason"),
    [("pending", REASON_NOT_READY), ("blocked", REASON_BLOCKED), ("weird", REASON_UNKNOWN)],
)
def test_other_statuses_are_named_not_assumed(status, reason):
    admission = admit_scanned_media(
        status, settings(provider="development", acknowledged=True)
    )
    assert admission.usable is False
    assert admission.reason_code == reason


def test_a_development_environment_may_use_its_own_stub():
    # app_env=test/development declares the stub by definition; production and
    # staging must say so explicitly (the case above).
    admission = admit_scanned_media(
        "skipped_dev", settings(provider="development", app_env="test")
    )
    assert admission.usable is True
    assert admission.demo_stub_skip is True


# ------------------------------------------------------- work-order media gate
def work_order_service(record_, *, settings_, logger=None):
    service = WorkOrderService.__new__(WorkOrderService)
    service._settings = settings_
    service._repository = MagicMock()
    service._repository.list_work_orders_for_file.return_value = ()
    service._storage = MagicMock()
    service._storage.get_object.return_value = JPEG
    return service


def test_work_order_read_refuses_unacknowledged_stub_media():
    service = work_order_service(record(), settings_=settings(acknowledged=False))
    with pytest.raises(AppError) as caught:
        service._read_stored_file(record())

    error = caught.value
    assert error.status_code == 409
    assert error.details == {
        "reason": "MEDIA_SCAN_STUB_UNACKNOWLEDGED",
        "scanner_status": "skipped_dev",
    }
    service._storage.get_object.assert_not_called()


def test_work_order_read_uses_demo_media_but_records_the_skip(caplog):
    service = work_order_service(record(), settings_=settings(acknowledged=True))
    with caplog.at_level("WARNING"):
        assert service._read_stored_file(record()) == JPEG
    skipped = scan_events(caplog)
    assert len(skipped) == 1
    event = skipped[0]
    assert event["file_id"] == 31
    assert event["reason_code"] == REASON_DEMO_STUB
    assert event["media_scanned"] is False


def test_work_order_read_passes_a_real_verdict_without_logging(caplog):
    service = work_order_service(record(status="clean"), settings_=settings(provider="clamav"))
    with caplog.at_level("WARNING"):
        assert service._read_stored_file(record(status="clean")) == JPEG
    assert scan_events(caplog) == []


# --------------------------------------------------------- suggestion position
def suggestion_service(*, settings_):
    service = DescriptionSuggestionService.__new__(DescriptionSuggestionService)
    service._settings = settings_
    service._limiter = MagicMock()
    service._vlm = MagicMock()
    service._vlm.inspect_image.return_value = SimpleNamespace(description="楼道灯不亮")
    service._asr = MagicMock()
    service._repository = MagicMock()
    service._suggestions = MagicMock()
    return service


def test_suggestion_refuses_unacknowledged_stub_media_with_a_reason():
    service = suggestion_service(settings_=settings(acknowledged=False))
    suggestion = service._suggest_from(record(), "vlm")

    assert suggestion.text == ""
    assert suggestion.source == "none"
    assert suggestion.reason_code == "MEDIA_SCAN_STUB_UNACKNOWLEDGED"
    # And the reporter gets a sentence, not a bare code.
    assert suggestion_reason(suggestion.reason_code) != "未能生成描述，请手动填写。"
    service._vlm.inspect_image.assert_not_called()


def test_suggestion_uses_demo_media_and_records_the_skip(caplog):
    service = suggestion_service(settings_=settings(acknowledged=True))
    with caplog.at_level("WARNING"):
        suggestion = service._suggest_from(record(), "vlm")

    assert isinstance(suggestion, DescriptionSuggestion)
    assert suggestion.text == "楼道灯不亮"
    assert suggestion.source == "vlm"
    assert len(scan_events(caplog)) == 1
