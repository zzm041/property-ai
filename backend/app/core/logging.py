"""Structured JSON logging configuration."""

import logging
import sys
from typing import Any

import structlog

_REDACT_TOKENS = (
    "authorization",
    "password",
    "secret",
    "api_key",
    "apikey",
    "session_key",
    "access_token",
    "refresh_token",
    "jwt",
    "private_key",
    "credential",
    "js_code",
    "wx_code",
    "app_secret",
)


def redact_log_event(
    _logger: Any,
    _method: str,
    event_dict: dict[str, Any],
) -> dict[str, Any]:
    """Drop secrets from structured log events. Never log tokens or provider keys."""

    for key, value in list(event_dict.items()):
        lowered = str(key).casefold()
        if any(token in lowered for token in _REDACT_TOKENS):
            event_dict[key] = "[redacted]"
            continue
        if isinstance(value, str) and value.casefold().startswith("bearer "):
            event_dict[key] = "[redacted]"
    return event_dict


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
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=False,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
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
