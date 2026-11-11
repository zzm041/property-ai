"""File scanning boundary for uploaded media."""

from __future__ import annotations

import re
from ipaddress import ip_address
import socket
import struct
import time
from typing import Literal

from app.core.logging import get_logger


logger = get_logger(__name__)

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


class ClamAVFileScannerProvider(FileScannerProvider):
    """Stream media to a deployment-controlled, private clamd endpoint.

    TCP clamd has no authentication/TLS. Deployment must restrict access; never
    expose it publicly. No filenames, paths or response text enter logs.
    """

    def __init__(self, *, host: str, port: int, timeout_seconds: float, max_bytes: int):
        address = ip_address(host)
        self._host = str(address)
        self._family = socket.AF_INET6 if address.version == 6 else socket.AF_INET
        self._port = port
        self._timeout_seconds = timeout_seconds
        self._max_bytes = max_bytes

    @staticmethod
    def _pending(reason: str) -> FileScanStatus:
        logger.warning("file_scan_pending", provider="clamav", reason=reason)
        return "pending"

    def scan(self, *, content: bytes, mime_type: str) -> FileScanStatus:
        del mime_type  # MIME validation belongs to the upload service.
        if not content or len(content) > self._max_bytes:
            return self._pending("invalid_size")
        deadline = time.monotonic() + self._timeout_seconds

        def remaining() -> float:
            value = deadline - time.monotonic()
            if value <= 0:
                raise TimeoutError
            return value

        try:
            # Numeric addresses avoid blocking DNS and per-address timeout reset.
            with socket.socket(self._family, socket.SOCK_STREAM) as conn:
                conn.settimeout(remaining())
                conn.connect((self._host, self._port))
                def send(data: bytes) -> None:
                    conn.settimeout(remaining())
                    conn.sendall(data)

                send(b"zINSTREAM\0")
                for offset in range(0, len(content), 65536):
                    chunk = content[offset:offset + 65536]
                    send(struct.pack("!I", len(chunk)) + chunk)
                send(struct.pack("!I", 0))
                response = bytearray()
                while len(response) <= 4096:
                    conn.settimeout(remaining())
                    part = conn.recv(4097 - len(response))
                    if not part:
                        return self._pending("incomplete_response")
                    response.extend(part)
                    if len(response) > 4096:
                        break
                    if b"\0" in response:
                        raw = bytes(response)
                        if raw == b"stream: OK\0":
                            return "clean"
                        if re.fullmatch(rb"stream: [\x21-\x7e][\x20-\x7e]* FOUND\x00", raw):
                            return "blocked"
                        return self._pending("invalid_response")
                return self._pending("response_too_large")
        except TimeoutError:
            return self._pending("timeout")
        except OSError:
            return self._pending("connection_failed")
