"""Day 3 structured logging privacy and required fields."""

import json
from typing import Any

from structlog.contextvars import bind_contextvars, clear_contextvars

from app.core.logging import configure_logging, get_logger, redact_log_event


def test_redact_log_event_strips_secret_keys() -> None:
    event = redact_log_event(
        None,
        "info",
        {
            "event": "login",
            "authorization": "Bearer abc.def",
            "password": "plain",
            "wechat_app_secret": "wx-secret",
            "api_key": "sk-live",
            "session_key": "sess",
            "js_code": "08123",
            "safe": "work_order_id",
        },
    )
    assert event["authorization"] == "[redacted]"
    assert event["password"] == "[redacted]"
    assert event["wechat_app_secret"] == "[redacted]"
    assert event["api_key"] == "[redacted]"
    assert event["session_key"] == "[redacted]"
    assert event["js_code"] == "[redacted]"
    assert event["safe"] == "work_order_id"


def test_bearer_strings_are_redacted_even_on_unknown_keys() -> None:
    event = redact_log_event(
        None,
        "info",
        {"event": "proxy", "token_header": "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"},
    )
    assert event["token_header"] == "[redacted]"


def test_json_logs_include_service_timestamp_level(capsys: Any) -> None:
    configure_logging(service="worker")
    clear_contextvars()
    bind_contextvars(
        request_id="corr-1",
        service="worker",
        task_id="task-99",
        work_order_id=7,
        operation="app.worker.tasks.analyze_work_order_task",
    )
    get_logger("test").warning(
        "celery_task_failed",
        error_code="AI.TIMEOUT",
        authorization="Bearer leak",
    )
    output = capsys.readouterr().out.strip().splitlines()
    event = json.loads(output[-1])
    assert event["event"] == "celery_task_failed"
    assert event["level"] == "warning"
    assert event["timestamp"].endswith("Z")
    assert event["service"] == "worker"
    assert event["request_id"] == "corr-1"
    assert event["task_id"] == "task-99"
    assert event["work_order_id"] == 7
    assert event["error_code"] == "AI.TIMEOUT"
    assert event["authorization"] == "[redacted]"
    clear_contextvars()
