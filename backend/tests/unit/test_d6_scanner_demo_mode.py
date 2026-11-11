"""D6: the scanner stub must never be reported as a working scan pipeline.

`development` returns `skipped_dev` without reading a byte, so media becomes
usable but unverified. `/readyz` used to call that usable and stayed `ready` on
staging - a silent lie an operator could only discover by reading source. The
report now names three mutually exclusive modes and only `real` sets
`media_scanned`.
"""

from app.core.config import Settings
from app.core.readiness import check_readiness

NO_PROBES = {"mysql": (True, lambda _s: None)}
CLAMAV = {"file_scanner_provider": "clamav", "file_scanner_host": "127.0.0.1"}


def settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, **overrides)


def test_stub_on_staging_is_unacknowledged_and_degrades_readiness() -> None:
    settings_ = settings(app_env="staging", file_scanner_provider="development")
    report = settings_.media_pipeline()

    assert report["scan_mode"] == "demo_stub"
    assert report["media_scanned"] is False
    assert report["media_becomes_usable"] is False
    assert report["demo_stub_unacknowledged"] is True
    assert report["reason_code"] == "MEDIA_SCAN_STUB_UNACKNOWLEDGED"
    assert "FILE_SCANNER_DEMO_ACKNOWLEDGED=true" in report["note"]

    payload, http_status = check_readiness(settings_, NO_PROBES, include_queue=False)
    assert http_status == 200
    assert payload["status"] == "degraded"
    assert payload["media_pipeline"]["reason_code"] == (
        "MEDIA_SCAN_STUB_UNACKNOWLEDGED"
    )


def test_acknowledged_demo_mode_is_still_not_a_scanned_pipeline() -> None:
    settings_ = settings(
        app_env="staging",
        file_scanner_provider="development",
        file_scanner_demo_acknowledged=True,
    )
    report = settings_.media_pipeline()

    assert report["scan_mode"] == "demo_stub"
    assert report["demo_stub_acknowledged"] is True
    assert report["demo_stub_unacknowledged"] is False
    assert report["media_scanned"] is False
    assert report["media_becomes_usable"] is False
    assert report["reason_code"] == "MEDIA_SCAN_STUB"

    payload, _ = check_readiness(settings_, NO_PROBES, include_queue=False)
    # Declared demo mode is honest, so readiness is not degraded - but the
    # payload still says the media was not scanned.
    assert payload["status"] == "ready"
    assert payload["media_pipeline"]["media_scanned"] is False


def test_production_treats_the_stub_as_a_misconfiguration() -> None:
    # Built as staging and relabelled, because Settings has unrelated production
    # validators (WeChat credentials) that have nothing to do with the scanner.
    staging = settings(app_env="staging", file_scanner_provider="development")
    report = staging.model_copy(update={"app_env": "production"}).media_pipeline()

    assert report["demo_stub_unacknowledged"] is True
    assert report["reason_code"] == "MEDIA_SCAN_STUB_UNACKNOWLEDGED"


def test_development_environment_reports_the_stub_without_degrading() -> None:
    settings_ = settings(app_env="development", file_scanner_provider="development")
    report = settings_.media_pipeline()

    assert report["scan_mode"] == "demo_stub"
    assert report["demo_stub_unacknowledged"] is False
    assert report["reason_code"] == "MEDIA_SCAN_STUB"

    payload, _ = check_readiness(settings_, NO_PROBES, include_queue=False)
    assert payload["status"] == "ready"


def test_only_a_real_verdict_claims_the_media_was_scanned() -> None:
    real = settings(**CLAMAV).media_pipeline()
    assert real["scan_mode"] == "real"
    assert real["media_scanned"] is True
    assert real["media_becomes_usable"] is True
    assert real["reason_code"] is None
    assert real["note"] is None

    closed = settings(file_scanner_provider="unavailable").media_pipeline()
    assert closed["scan_mode"] == "unavailable"
    assert closed["media_scanned"] is False
    assert closed["media_becomes_usable"] is False
    assert closed["reason_code"] == "MEDIA_SCAN_UNAVAILABLE"


def test_every_mode_sets_a_reason_except_real_scanning() -> None:
    for overrides, expected in (
        ({"file_scanner_provider": "unavailable"}, "MEDIA_SCAN_UNAVAILABLE"),
        ({"file_scanner_provider": "development"}, "MEDIA_SCAN_STUB"),
        (CLAMAV, None),
    ):
        report = settings(app_env="test", **overrides).media_pipeline()
        assert report["reason_code"] == expected
