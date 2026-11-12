"""t227 (R2-1): /readyz must not call a reachable-but-unusable adapter `ok`.

The frozen RAG launcher answers `200` on `/healthz` while it is draining or while
its reservation gate refuses new work; the only visible symptom used to be a
`knowledge_unavailable` answer minutes later. These tests pin the corrected
contract: the probe reads the healthz body, votes `down` when the gate is not
READY (or admission is closed), and publishes the runtime block as `ai_runtime`.
"""

from __future__ import annotations

import http.server
import json
import threading
from functools import partial

import pytest

from app.core.config import Settings
from app.core.readiness import (
    AI_RUNTIME_READY,
    ai_runtime_snapshot,
    check_readiness,
    probe_ai_endpoint,
)

READY_BODY = {
    "status": "ready",
    "inference": False,
    "scope": "rag_only",
    "models": {
        "embedding": "bge-m3",
        "reranker": "bge-reranker-v2-m3",
        "qwen": "qwen3.5:4b",
    },
    "reservation_gate": "READY",
    "accepting_requests": True,
    "request_active": False,
}


def _settings(**overrides) -> Settings:
    base = dict(
        _env_file=None,
        app_env="staging",
        database_url="mysql+pymysql://u:p@mysql:3306/property_ai_delivery_check",
        wechat_login_enabled=False,
    )
    base.update(overrides)
    return Settings(**base)


def _serve(payload: object) -> tuple[http.server.ThreadingHTTPServer, int]:
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - http.server contract
            if self.path == "/healthz":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_response(404)
            self.end_headers()

        def log_message(self, *args) -> None:
            return

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, server.server_address[1]


def _embedding_settings(port: int) -> Settings:
    return _settings(
        embedding_provider="production",
        embedding_endpoint=f"http://127.0.0.1:{port}/v1",
        embedding_api_key="local-token",
    )


def test_ready_adapter_returns_the_runtime_block() -> None:
    server, port = _serve(READY_BODY)
    try:
        outcome = probe_ai_endpoint(_embedding_settings(port), "embedding")
    finally:
        server.shutdown()

    assert isinstance(outcome, dict)
    assert outcome["status"] == "up"
    runtime = outcome["runtime"]
    assert runtime["reservation_gate"] == AI_RUNTIME_READY
    assert runtime["accepting_requests"] is True
    assert runtime["scope"] == "rag_only"
    assert runtime["models"]["embedding"] == "bge-m3"


def test_reservation_gate_that_is_not_ready_makes_the_provider_down() -> None:
    blocked = dict(READY_BODY, reservation_gate="BUDGET_REJECTED")
    server, port = _serve(blocked)
    try:
        with pytest.raises(RuntimeError) as caught:
            probe_ai_endpoint(_embedding_settings(port), "embedding")
    finally:
        server.shutdown()

    assert "reservation gate BUDGET_REJECTED" in str(caught.value)


def test_draining_adapter_is_down() -> None:
    draining = dict(READY_BODY, accepting_requests=False)
    server, port = _serve(draining)
    try:
        with pytest.raises(RuntimeError) as caught:
            probe_ai_endpoint(_embedding_settings(port), "embedding")
    finally:
        server.shutdown()

    assert "not accepting requests" in str(caught.value)


def test_adapter_reporting_a_non_ready_status_is_down() -> None:
    broken = dict(READY_BODY, status="degraded")
    server, port = _serve(broken)
    try:
        with pytest.raises(RuntimeError) as caught:
            probe_ai_endpoint(_embedding_settings(port), "embedding")
    finally:
        server.shutdown()

    assert "adapter status degraded" in str(caught.value)


def test_non_json_healthz_body_still_counts_as_up() -> None:
    server, port = _serve(b"OK")
    try:
        assert probe_ai_endpoint(_embedding_settings(port), "embedding") == "up"
    finally:
        server.shutdown()


def test_unconfigured_provider_never_probes() -> None:
    settings = _settings(embedding_provider="development", embedding_endpoint="")
    assert probe_ai_endpoint(settings, "embedding") == "not_configured"


def test_readyz_publishes_the_runtime_block() -> None:
    server, port = _serve(READY_BODY)
    try:
        result, status = check_readiness(
            _embedding_settings(port),
            probes={
                "mysql": (True, lambda _settings: None),
                "embedding": (False, partial(probe_ai_endpoint, name="embedding")),
            },
            include_queue=False,
        )
    finally:
        server.shutdown()

    assert status == 200
    assert result["status"] == "ready"
    assert result["ai_providers"]["embedding"] == "ok"
    assert result["ai_runtime"]["reservation_gate"] == AI_RUNTIME_READY
    assert result["ai_runtime"]["accepting_requests"] is True
    assert result["degraded_reasons"] == []


def test_readyz_names_a_gate_that_blocks_work() -> None:
    blocked = dict(READY_BODY, reservation_gate="NOT_READY")
    server, port = _serve(blocked)
    try:
        result, status = check_readiness(
            _embedding_settings(port),
            probes={
                "mysql": (True, lambda _settings: None),
                "embedding": (False, partial(probe_ai_endpoint, name="embedding")),
            },
            include_queue=False,
        )
    finally:
        server.shutdown()

    assert status == 200
    assert result["status"] == "degraded"
    assert result["ai_providers"]["embedding"] == "down"
    assert "ai_provider_down:embedding" in result["degraded_reasons"]
    # A down probe publishes no runtime block; readiness never invents one.
    assert result["ai_runtime"] == {}


def test_unconfigured_providers_publish_no_runtime() -> None:
    result, status = check_readiness(
        _settings(embedding_provider="development", embedding_endpoint=""),
        probes={
            "mysql": (True, lambda _settings: None),
            "embedding": (False, partial(probe_ai_endpoint, name="embedding")),
        },
        include_queue=False,
    )

    assert status == 200
    assert result["status"] == "ready"
    assert result["ai_providers"]["embedding"] == "not_configured"
    assert result["ai_runtime"] == {}


def test_snapshot_only_keeps_known_keys() -> None:
    payload = dict(READY_BODY, secret="do-not-leak", token="also-secret")

    snapshot = ai_runtime_snapshot(payload)

    assert "secret" not in snapshot
    assert "token" not in snapshot
    assert set(snapshot) <= {
        "scope",
        "status",
        "inference",
        "reservation_gate",
        "accepting_requests",
        "request_active",
        "models",
    }
