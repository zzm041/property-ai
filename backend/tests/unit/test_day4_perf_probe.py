"""Perf probe parses explicit millisecond headers and does not guess units."""

import importlib.util
from pathlib import Path

from httpx import Headers


def _load_probe():
    script = Path(__file__).resolve().parents[3] / "scripts" / "ops" / "perf_probe.py"
    spec = importlib.util.spec_from_file_location("day4_perf_probe", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_process_time_ms_header_is_milliseconds_without_magnitude_guess() -> None:
    probe = _load_probe()
    assert probe.parse_server_process_ms(Headers({"x-process-time-ms": "8.5"})) == 8.5
    assert probe.parse_server_process_ms(Headers({"x-process-time-ms": "12.25"})) == 12.25
    assert probe.parse_server_process_ms(Headers({"server-timing": "app;dur=8.5"})) == 8.5
    assert probe.parse_server_process_ms(Headers({"server-timing": "app;dur=12.25"})) == 12.25
    assert probe.parse_server_process_ms(Headers({"x-process-time": "8.5"})) is None


def test_probe_source_does_not_discard_server_ms_or_guess_units() -> None:
    source = (
        Path(__file__).resolve().parents[3] / "scripts" / "ops" / "perf_probe.py"
    ).read_text(encoding="utf-8")
    assert "latency, status, _server" not in source
    assert "1000 if" not in source
    assert "server_process_p50_ms" in source
    assert "request_id_echo_mismatches" in source
    assert "httpx response.elapsed" in source
