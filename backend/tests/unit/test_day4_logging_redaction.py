"""Rendered log output must redact nested secrets and exception chains."""

import json
import logging
from typing import Any

import httpx
from structlog.contextvars import bind_contextvars, clear_contextvars

from app.core.logging import configure_logging, get_logger, redact_log_event, safe_url_for_log
from app.provider.http import request_json


JWT = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.aaaaaaaaaa.bbbbbbbbbb"
WX_URL = (
    "https://api.weixin.qq.com/sns/jscode2session"
    "?appid=wx123&secret=super-wechat-secret&js_code=0812345678"
)


def test_json_string_and_dict_repr_secrets_are_redacted() -> None:
    event = redact_log_event(
        None,
        "error",
        {
            "event": "upstream",
            "payload": '{"password": "plain-pass", "api_key": "sk-live", "session_key": "wx-sess"}',
            "repr": "{'password': 'hunter2', 'api_key': 'sk-other'}",
        },
    )
    rendered = str(event)
    assert "plain-pass" not in rendered
    assert "sk-live" not in rendered
    assert "wx-sess" not in rendered
    assert "hunter2" not in rendered
    assert "sk-other" not in rendered


def test_exception_chain_and_stdlib_keep_error_code_not_raw_secret(capsys: Any) -> None:
    configure_logging(service="api")
    try:
        raise RuntimeError('upstream {"password": "leak-pass", "session_key": "wx-leak"}')
    except RuntimeError as exc:
        try:
            raise ValueError("wrapper") from exc
        except ValueError as wrapped:
            get_logger("test").error(
                "provider_chain",
                error_code="AI.UNAVAILABLE",
                operation="jscode2session",
                exc_info=wrapped,
            )
    output = capsys.readouterr().out
    assert "leak-pass" not in output
    assert "wx-leak" not in output
    assert "AI.UNAVAILABLE" in output
    assert "jscode2session" in output


def test_nested_structures_are_redacted() -> None:
    event = redact_log_event(
        None,
        "info",
        {
            "event": "proxy",
            "headers": {"Authorization": f"Bearer {JWT}", "X-Request-Id": "r1"},
            "items": [{"password": "plain", "safe": "ok"}],
            "llm_api_key": "sk-live",
            "database_url": "mysql+pymysql://user:pass@localhost/db",
        },
    )
    assert event["headers"]["Authorization"] == "[redacted]"
    assert event["headers"]["X-Request-Id"] == "r1"
    assert event["items"][0]["password"] == "[redacted]"
    assert event["items"][0]["safe"] == "ok"
    assert event["llm_api_key"] == "[redacted]"
    assert "pass" not in str(event["database_url"])


def test_rendered_traceback_redacts_jwt_and_keeps_error_code(capsys: Any) -> None:
    configure_logging(service="api")
    clear_contextvars()
    bind_contextvars(request_id="corr-secret", service="api")
    try:
        raise RuntimeError(f"Authorization: Bearer {JWT}")
    except RuntimeError as exc:
        get_logger("test").error(
            "unhandled_exception",
            error_code="SYS.INTERNAL_ERROR",
            operation="work_order.create",
            exc_info=exc,
        )
    output = capsys.readouterr().out
    assert JWT not in output
    assert "Bearer eyJ" not in output
    event = None
    for line in output.strip().splitlines():
        if "unhandled_exception" in line:
            event = json.loads(line)
    assert event is not None
    assert event["error_code"] == "SYS.INTERNAL_ERROR"
    assert event["operation"] == "work_order.create"
    assert event["request_id"] == "corr-secret"
    clear_contextvars()


def test_stdlib_and_worker_logs_redact_secrets(capsys: Any) -> None:
    configure_logging(service="worker")
    logging.getLogger("celery").error("token %s", JWT)
    logging.getLogger("test.stdlib").error("wx %s", WX_URL)
    output = capsys.readouterr().out
    assert JWT not in output
    assert "super-wechat-secret" not in output
    assert "0812345678" not in output


def test_provider_http_exception_chain_redacts_wechat_url(capsys: Any, monkeypatch) -> None:
    configure_logging(service="api")

    class BoomClient:
        def request(self, *args: object, **kwargs: object) -> object:
            raise httpx.ConnectError("failed", request=httpx.Request("GET", WX_URL))

        def close(self) -> None:
            return None

    try:
        request_json(
            method="GET",
            url=WX_URL,
            timeout_seconds=1.0,
            client=BoomClient(),  # type: ignore[arg-type]
            provider="wechat",
            operation="jscode2session",
        )
    except Exception:
        pass
    output = capsys.readouterr().out
    assert "super-wechat-secret" not in output
    assert "0812345678" not in output
    assert "jscode2session" in output
    assert "AI.UNAVAILABLE" in output


def test_safe_url_strips_userinfo_and_query() -> None:
    redacted = safe_url_for_log("https://user:secret@api.example.com/path?token=abc")
    assert "secret" not in redacted
    assert "abc" not in redacted
    assert "api.example.com" in redacted
    assert "token" not in redacted
