"""Structured logging tests."""

import json
from typing import Any

from structlog.contextvars import bind_contextvars, clear_contextvars

from app.core.logging import configure_logging, get_logger


def test_application_log_is_json_with_request_id(capsys: Any) -> None:
    configure_logging()
    clear_contextvars()
    bind_contextvars(request_id="log-request-123")

    get_logger("test").info("test_event", latency_ms=12.5)

    output = capsys.readouterr().out.strip().splitlines()
    event = json.loads(output[-1])
    assert event["event"] == "test_event"
    assert event["request_id"] == "log-request-123"
    assert event["latency_ms"] == 12.5
    assert event["level"] == "info"
    assert event["timestamp"].endswith("Z")
    clear_contextvars()
