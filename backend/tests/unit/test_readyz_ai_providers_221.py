"""t221: /readyz must tell the truth about configured AI providers.

Before this change every model provider was registered as `NOT_CHECKED`
(`live_vendor_not_probed`), so stopping the frozen AI adapter left `/readyz`
answering `ready` while every analysis and RAG call was doomed. These tests pin
the corrected contract: configured providers are probed (2 s), an unreachable
configured provider degrades readiness *and is named*, and an unconfigured
provider reports `not_configured` instead of pretending to be up.
"""

from __future__ import annotations

import http.server
import inspect
import threading
from functools import partial

import pytest

from app.core import readiness
from app.core.config import Settings
from app.core.readiness import (
    AI_PROVIDER_TIMEOUT_SECONDS,
    ai_health_url,
    check_readiness,
    probe_ai_endpoint,
)


def _settings(**overrides) -> Settings:
    base = dict(
        _env_file=None,
        app_env="staging",
        database_url="mysql+pymysql://u:p@mysql:3306/property_ai_delivery_check",
        wechat_login_enabled=False,
    )
    base.update(overrides)
    return Settings(**base)


def _ok(_settings) -> None:
    return None


def _boom(_settings) -> None:
    raise ConnectionError("adapter stopped")


def _stub_adapter() -> tuple[http.server.ThreadingHTTPServer, int]:
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - http.server contract
            if self.path == "/healthz":
                body = b'{"status":"ready"}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_response(404)
            self.end_headers()

        def log_message(self, *args) -> None:  # keep pytest output clean
            return

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, server.server_address[1]


def test_all_configured_providers_up_is_ready() -> None:
    result, status = check_readiness(
        _settings(),
        probes={
            "mysql": (True, _ok),
            "llm": (False, _ok),
            "vlm": (False, _ok),
            "asr": (False, _ok),
            "embedding": (False, _ok),
            "reranker": (False, _ok),
        },
        include_queue=False,
    )

    assert status == 200
    assert result["status"] == "ready"
    assert result["ai_providers"] == {
        "llm": "ok",
        "vlm": "ok",
        "asr": "ok",
        "embedding": "ok",
        "rerank": "ok",
    }
    assert result["degraded_reasons"] == []


def test_llm_down_degrades_and_names_the_provider() -> None:
    result, status = check_readiness(
        _settings(),
        probes={
            "mysql": (True, _ok),
            "llm": (False, _boom),
            "vlm": (False, _ok),
            "asr": (False, _ok),
            "embedding": (False, _ok),
            "reranker": (False, _ok),
        },
        include_queue=False,
    )

    assert status == 200
    assert result["status"] == "degraded"
    assert result["ai_providers"]["llm"] == "down"
    assert result["ai_providers"]["vlm"] == "ok"
    assert result["ai_providers"]["rerank"] == "ok"
    assert "ai_provider_down:llm" in result["degraded_reasons"]
    assert result["ai_provider_details"]["llm"]["status"] == "down"
    # no secret ever reaches the payload
    assert "adapter stopped" not in str(result)


def test_unconfigured_providers_report_not_configured() -> None:
    probes = {
        readiness_name: (False, partial(probe_ai_endpoint, name=name))
        for name, readiness_name in (
            ("llm", "llm"),
            ("vlm", "vlm"),
            ("asr", "asr"),
            ("embedding", "embedding"),
            ("rerank", "reranker"),
        )
    }
    probes["mysql"] = (True, _ok)
    result, status = check_readiness(_settings(), probes=probes, include_queue=False)

    assert status == 200
    # `development` providers and empty endpoints are not configured endpoints,
    # so readiness stays honest without inventing a failure.
    assert result["status"] == "ready"
    assert set(result["ai_providers"].values()) == {"not_configured"}


def test_default_probe_table_no_longer_skips_model_providers() -> None:
    source = inspect.getsource(check_readiness)
    for name in ("llm", "vlm", "asr", "embedding", "reranker"):
        assert f'"{name}": (False, NOT_CHECKED)' not in source
    assert 'partial(probe_ai_endpoint, name="llm")' in source
    assert 'partial(probe_ai_endpoint, name="rerank")' in source


@pytest.mark.parametrize(
    ("endpoint", "expected"),
    [
        ("http://host.docker.internal:11437/v1", "http://host.docker.internal:11437/healthz"),
        ("http://host.docker.internal:11437/v1/", "http://host.docker.internal:11437/healthz"),
        ("http://127.0.0.1:11436", "http://127.0.0.1:11436/healthz"),
        ("  http://127.0.0.1:8000/v1  ", "http://127.0.0.1:8000/healthz"),
        # the reranker is configured per operation, not per host (t223)
        ("http://host.docker.internal:11436/v1/rerank", "http://host.docker.internal:11436/healthz"),
        ("", ""),
    ],
)
def test_health_url_maps_provider_base_urls(endpoint: str, expected: str) -> None:
    assert ai_health_url(endpoint) == expected


def test_probe_reports_up_against_a_live_adapter() -> None:
    server, port = _stub_adapter()
    try:
        settings = _settings(
            llm_provider="production",
            llm_base_url=f"http://127.0.0.1:{port}/v1",
            llm_api_key="probe-key",
        )
        # t227: a live adapter may also publish its runtime block, so the probe
        # answers either the bare verdict or `{"status": "up", "runtime": {...}}`.
        outcome = probe_ai_endpoint(settings, "llm")
        status = outcome["status"] if isinstance(outcome, dict) else outcome
        assert status == "up"
    finally:
        server.shutdown()
        server.server_close()


def test_probe_raises_when_a_configured_adapter_is_stopped() -> None:
    settings = _settings(
        llm_provider="production",
        llm_base_url="http://127.0.0.1:9/v1",
        llm_api_key="probe-key",
    )
    with pytest.raises(Exception):
        probe_ai_endpoint(settings, "llm")


def test_probe_ignores_development_providers_even_with_an_endpoint() -> None:
    settings = _settings(
        llm_provider="development",
        llm_base_url="http://127.0.0.1:9/v1",
    )
    assert probe_ai_endpoint(settings, "llm") == "not_configured"


def test_probe_timeout_is_bounded_to_two_seconds() -> None:
    assert AI_PROVIDER_TIMEOUT_SECONDS == 2.0
