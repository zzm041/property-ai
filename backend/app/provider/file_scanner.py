"""File scanning boundary for uploaded media."""

from __future__ import annotations

from typing import Literal


FileScanStatus = Literal["pending", "clean", "blocked", "skipped_dev"]


class FileScannerProvider:
    def scan(self, *, content: bytes, mime_type: str) -> FileScanStatus:
        raise NotImplementedError


class DevelopmentFileScannerProvider(FileScannerProvider):
    """Explicit local-only scanner stub; never represents a malware scan."""

    def scan(self, *, content: bytes, mime_type: str) -> FileScanStatus:
        del content, mime_type
        return "skipped_dev"


class UnavailableFileScannerProvider(FileScannerProvider):
    """Fail closed until an approved production scanner is configured."""

    def scan(self, *, content: bytes, mime_type: str) -> FileScanStatus:
        del content, mime_type
        return "pending"
