"""Clamd protocol tests with synthetic replies; no installed scanner is used."""
from contextlib import contextmanager
import socket
import struct
from threading import Thread
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.provider.factory import build_file_scanner_provider


def scanner(**overrides):
    settings = Settings(_env_file=None, app_env="test", file_scanner_provider="clamav",
                        file_scanner_host="127.0.0.1", **overrides)
    return build_file_scanner_provider(settings)


@contextmanager
def daemon(reply_parts):
    """Capture actual INSTREAM framing on an ephemeral loopback socket."""
    captured = bytearray()
    errors = []
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(3)

        def serve():
            try:
                with listener.accept()[0] as conn:
                    conn.settimeout(3)

                    def read_exact(size):
                        data = bytearray()
                        while len(data) < size:
                            part = conn.recv(size - len(data))
                            if not part:
                                raise AssertionError("client closed a partial frame")
                            data.extend(part)
                        return bytes(data)

                    assert read_exact(10) == b"zINSTREAM\0"
                    while size := struct.unpack("!I", read_exact(4))[0]:
                        assert size <= 65536
                        captured.extend(read_exact(size))
                    for part in reply_parts:
                        conn.sendall(part)
            except Exception as exc:
                errors.append(exc)

        worker = Thread(target=serve, daemon=True)
        worker.start()
        try:
            yield listener.getsockname()[1], captured
        finally:
            worker.join(timeout=4)
            assert not worker.is_alive()
            assert not errors, errors


def test_real_socket_sends_bounded_chunks_and_accepts_complete_clean_reply():
    content = b"synthetic media" * 10000
    with daemon([b"stream: ", b"OK\0"]) as (port, captured):
        result = scanner(file_scanner_port=port).scan(content=content, mime_type="image/jpeg")
    assert result == "clean"
    assert captured == content


@pytest.mark.parametrize("response, expected", [
    (b"stream: Synthetic.Test.Signature FOUND\0", "blocked"),
    (b"stream: OK\0", "clean"),
    (b"stream: size limit exceeded ERROR\0", "pending"),
    (b"INSTREAM size limit exceeded. ERROR\0", "pending"),
    (b"OK\0", "pending"),
    (b"stream: OK", "pending"),
    (b"stream: OK\n", "pending"),
    (b"stream: OK\0stream: bad FOUND\0", "pending"),
    (b"stream: FOUND\0", "pending"),
    (b"stream: \xff FOUND\0", "pending"),
    (b"x" * 4097, "pending"),
    (b"", "pending"),
])
def test_untrusted_daemon_response_fails_closed(monkeypatch, response, expected):
    conn = MagicMock()
    conn.__enter__.return_value = conn
    conn.recv.side_effect = [response, b""]
    monkeypatch.setattr(socket, "socket", lambda *args, **kwargs: conn)
    assert scanner().scan(content=b"synthetic", mime_type="audio/wav") == expected


@pytest.mark.parametrize("error", [TimeoutError(), ConnectionRefusedError(), OSError("synthetic")])
def test_connection_failures_remain_pending_without_retry(monkeypatch, error):
    conn = MagicMock()
    conn.__enter__.return_value = conn
    conn.connect.side_effect = error
    monkeypatch.setattr(socket, "socket", lambda *args, **kwargs: conn)
    assert scanner().scan(content=b"synthetic", mime_type="image/png") == "pending"
    assert conn.connect.call_count == 1


def test_empty_or_oversized_content_never_opens_socket(monkeypatch):
    connect = MagicMock()
    monkeypatch.setattr(socket, "socket", connect)
    provider = scanner(upload_max_mb=1)
    for content in (b"", b"x" * (1024 * 1024 + 1)):
        assert provider.scan(content=content, mime_type="image/png") == "pending"
    connect.assert_not_called()


@pytest.mark.parametrize("host", ["", "https://scanner.local", "scanner.local/path", "scanner\nlocal", "scanner local", "scanner.local", "8.8.8.8", "0.0.0.0", "224.0.0.1"])
def test_clamav_requires_explicit_safe_host(host):
    with pytest.raises(ValidationError, match="scanner"):
        Settings(_env_file=None, file_scanner_provider="clamav", file_scanner_host=host)


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "192.168.50.10", "fd00::10"])
def test_private_numeric_scanner_addresses_are_accepted(host):
    settings = Settings(_env_file=None, file_scanner_provider="clamav", file_scanner_host=host)
    assert build_file_scanner_provider(settings) is not None


def test_upload_scan_uses_one_total_deadline(monkeypatch):
    import app.provider.file_scanner as module

    clock = [0.0]
    conn = MagicMock()
    conn.__enter__.return_value = conn
    conn.sendall.side_effect = lambda _: clock.__setitem__(0, clock[0] + 0.6)
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(socket, "socket", lambda *args, **kwargs: conn)
    assert scanner(file_scanner_timeout_seconds=1).scan(content=b"synthetic", mime_type="image/png") == "pending"
    conn.recv.assert_not_called()


def test_scan_does_not_enter_unbounded_dns_resolution(monkeypatch):
    resolve = MagicMock(side_effect=AssertionError("DNS must not be called"))
    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    with daemon([b"stream: OK\0"]) as (port, _):
        assert scanner(file_scanner_port=port).scan(content=b"synthetic", mime_type="image/png") == "clean"
    resolve.assert_not_called()
