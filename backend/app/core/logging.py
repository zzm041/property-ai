"""Structured JSON logging configuration with recursive secret redaction."""

from __future__ import annotations

import logging
import re
import sys
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import structlog

_REDACTED = "[redacted]"
_REDACT_TOKENS = (
    "authorization",
    "password",
    "passwd",
    "secret",
    "api_key",
    "apikey",
    "session_key",
    "sessionkey",
    "access_token",
    "accesstoken",
    "refresh_token",
    "refreshtoken",
    "otp",
    "sms_code",
    "verification_code",
    "private_key",
    "privatekey",
    "credential",
    "js_code",
    "jscode",
    "wx_code",
    "wxcode",
    "app_secret",
    "appsecret",
    "database_url",
    "broker_url",
    "celery_broker",
)
_BEARER_RE = re.compile(r"bearer\s+[A-Za-z0-9._\-+=/]+", re.I)
_JWT_RE = re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")
_URL_USERINFO_RE = re.compile(r"([a-z][a-z0-9+.-]*://)([^/@\s]+):([^@/\s]+)@", re.I)
_QUERY_SECRET_RE = re.compile(
    r"(?i)(secret|password|passwd|api_key|access_token|js_code|code|token)=([^\s&\"']+)"
)
_JSON_SECRET_RE = re.compile(
    r'(?i)("(?:password|passwd|api_key|apikey|session_key|sessionkey|secret|authorization|access_token|refresh_token|otp|sms_code|verification_code|code)"\s*:\s*)"(?:\\.|[^"\\])*"'
)
_REPR_SECRET_RE = re.compile(
    r"(?i)(['\"]?(?:password|passwd|api_key|apikey|session_key|sessionkey|secret|authorization)['\"]?\s*[:=]\s*)(['\"])(?:\\.|[^'\\\"])*\2"
)


def _normalize_key(key: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(key).casefold())


def _key_is_sensitive(key: object) -> bool:
    lowered = _normalize_key(key)
    return any(_normalize_key(token) in lowered or lowered.endswith(_normalize_key(token)) for token in _REDACT_TOKENS)


def redact_text(value: str) -> str:
    """Redact secrets inside free-form strings, URLs, and tracebacks."""

    text = _BEARER_RE.sub(f"Bearer {_REDACTED}", value)
    text = _JWT_RE.sub(_REDACTED, text)
    text = _URL_USERINFO_RE.sub(r"\1" + _REDACTED + ":" + _REDACTED + "@", text)
    text = _QUERY_SECRET_RE.sub(lambda match: f"{match.group(1)}={_REDACTED}", text)
    text = _JSON_SECRET_RE.sub(rf'\1"{_REDACTED}"', text)
    text = _REPR_SECRET_RE.sub(lambda match: f"{match.group(1)}{match.group(2)}{_REDACTED}{match.group(2)}", text)
    return text


def redact_value(value: Any, *, key: object | None = None) -> Any:
    if key is not None and _key_is_sensitive(key):
        return _REDACTED
    if isinstance(value, dict):
        return {str(item_key): redact_value(item, key=item_key) for item_key, item in value.items()}
    if isinstance(value, (list, tuple)):
        redacted = [redact_value(item) for item in value]
        return type(value)(redacted) if not isinstance(value, list) else redacted
    if isinstance(value, str):
        if value.casefold().startswith("bearer "):
            return _REDACTED
        if "://" in value:
            return redact_text(value)
        return redact_text(value)
    if isinstance(value, BaseException):
        return redact_text(f"{type(value).__name__}: {value}")
    return value


def redact_log_event(
    _logger: Any,
    _method: str,
    event_dict: dict[str, Any],
) -> dict[str, Any]:
    """Drop secrets from structured log events, including nested payloads."""

    return redact_value(event_dict)


def redact_exc_info(
    _logger: Any,
    _method: str,
    event_dict: dict[str, Any],
) -> dict[str, Any]:
    """Redact secrets inside formatted exception chains and tracebacks."""

    if "exception" in event_dict:
        event_dict["exception"] = redact_value(event_dict["exception"])
    if "exc_info" in event_dict and not isinstance(event_dict["exc_info"], tuple):
        event_dict["exc_info"] = redact_value(event_dict["exc_info"])
    return event_dict


class _RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        formatted = super().format(record)
        return redact_text(formatted)


def configure_logging(*, debug: bool = False, service: str = "api") -> None:
    """Configure application and Uvicorn logs as one JSON stream."""

    level = logging.DEBUG if debug else logging.INFO
    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        redact_log_event,
    ]

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            redact_exc_info,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=False,
    )

    class _RedactingProcessorFormatter(structlog.stdlib.ProcessorFormatter):
        def format(self, record: logging.LogRecord) -> str:
            return redact_text(super().format(record))

    formatter = _RedactingProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.format_exc_info,
            redact_exc_info,
            structlog.processors.JSONRenderer(),
        ],
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(level)

    for logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access", "celery"):
        logger = logging.getLogger(logger_name)
        logger.handlers.clear()
        logger.propagate = True

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    structlog.contextvars.bind_contextvars(service=service)


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Return a named structured logger."""

    return structlog.get_logger(name)


def safe_url_for_log(url: str) -> str:
    """Return a URL with userinfo and sensitive query values removed."""

    split = urlsplit(url)
    hostname = split.hostname or ""
    netloc = hostname
    if split.port:
        netloc = f"{hostname}:{split.port}"
    return redact_text(urlunsplit((split.scheme, netloc, split.path, "", "")))
