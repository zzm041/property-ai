"""One admission rule for media that only exists if a scanner ran (D6).

PA-163 made "no scanner configured" visible in readiness; D6 made the
``development`` stub stop claiming ``media_becomes_usable``. The review then
found the other half still open: the *file* gate kept treating
``scanner_status=skipped_dev`` as ready, so an unacknowledged stub could still
push unexamined bytes into image/voice analysis and description suggestions.

The rule below is the single place that decides. Callers must not compare
``scanner_status`` themselves:

* ``clean``        - a real verdict, usable, nothing to log;
* ``skipped_dev``  - usable **only** when the deployment declares demo mode
                     (``FILE_SCANNER_PROVIDER=development`` acknowledged, or a
                     development/test environment). Every such use is reported
                     as ``media_scan_skipped`` so it is never silent;
* ``pending``      - not ready yet (upload has not been scanned);
* ``blocked``      - a verdict said no;
* anything else    - refused rather than assumed usable.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.config import Settings

#: A real scanner verdict. The only status that needs no explanation.
SCANNER_CLEAN = "clean"
#: Development stub: the file was never read by a scanner.
SCANNER_SKIPPED = "skipped_dev"
SCANNER_PENDING = "pending"
SCANNER_BLOCKED = "blocked"

#: ``reason_code`` carried by every admitted-but-unverified use.
REASON_DEMO_STUB = "MEDIA_SCAN_STUB"
REASON_NOT_READY = "MEDIA_NOT_READY"
REASON_BLOCKED = "MEDIA_SCAN_BLOCKED"
REASON_UNKNOWN = "MEDIA_SCAN_UNKNOWN"


@dataclass(frozen=True, slots=True)
class ScanAdmission:
    """What a caller may do with one file, and why."""

    usable: bool
    reason_code: str | None
    scanner_status: str
    scan_mode: str
    #: True when the media passes only because demo mode was declared: the
    #: caller must record ``media_scan_skipped`` before using the bytes.
    demo_stub_skip: bool = False


def admit_scanned_media(scanner_status: str | None, settings: Settings) -> ScanAdmission:
    """Decide whether unverified media may be used, and name the reason."""

    status = (scanner_status or "").strip()
    pipeline = settings.media_pipeline()
    mode = str(pipeline.get("scan_mode") or "unavailable")
    if status == SCANNER_CLEAN:
        return ScanAdmission(True, None, status, mode)
    if status == SCANNER_SKIPPED:
        stub_declared = mode == "demo_stub" and not pipeline.get("demo_stub_unacknowledged")
        if stub_declared:
            return ScanAdmission(True, REASON_DEMO_STUB, status, mode, demo_stub_skip=True)
        reason = str(pipeline.get("reason_code") or REASON_NOT_READY)
        return ScanAdmission(False, reason, status, mode)
    if status == SCANNER_PENDING:
        return ScanAdmission(False, REASON_NOT_READY, status, mode)
    if status == SCANNER_BLOCKED:
        return ScanAdmission(False, REASON_BLOCKED, status, mode)
    return ScanAdmission(False, REASON_UNKNOWN, status, mode)


def log_media_scan_skipped(logger, *, file_id: int | None, admission: ScanAdmission) -> None:
    """Report an admitted-but-unverified use; never let it pass silently."""

    logger.warning(
        "media_scan_skipped",
        file_id=file_id,
        scanner_status=admission.scanner_status,
        scan_mode=admission.scan_mode,
        reason_code=admission.reason_code,
        media_scanned=False,
    )
