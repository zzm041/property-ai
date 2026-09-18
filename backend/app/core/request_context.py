"""Request ID propagation and request lifecycle logging."""

import re
import time
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import uuid4

from structlog.contextvars import bind_contextvars, clear_contextvars

from app.core.logging import get_logger

ASGIApp = Callable[
    [dict[str, Any], Callable[..., Awaitable[Any]], Callable[..., Awaitable[Any]]],
    Awaitable[None],
]

REQUEST_ID_HEADER = b"x-request-id"
REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")

logger = get_logger(__name__)


def _request_id_from_scope(scope: dict[str, Any]) -> str:
    for name, value in scope.get("headers", []):
        if name.lower() != REQUEST_ID_HEADER:
            continue
        candidate = value.decode("ascii", errors="ignore")
        if REQUEST_ID_PATTERN.fullmatch(candidate):
            return candidate
    return uuid4().hex


class RequestContextMiddleware:
    """Attach one safe request ID to state, logs, body, and response headers."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: Callable[..., Awaitable[Any]],
        send: Callable[..., Awaitable[Any]],
    ) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = _request_id_from_scope(scope)
        scope.setdefault("state", {})["request_id"] = request_id
        clear_contextvars()
        bind_contextvars(request_id=request_id, service="api")

        method = scope.get("method", "")
        path = scope.get("path", "")
        started_at = time.perf_counter()
        status_code = 500

        async def send_with_request_id(message: dict[str, Any]) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message["status"])
                headers = [
                    (name, value)
                    for name, value in message.get("headers", [])
                    if name.lower() != REQUEST_ID_HEADER
                ]
                headers.append((REQUEST_ID_HEADER, request_id.encode("ascii")))
                process_ms = (time.perf_counter() - started_at) * 1000
                headers.append(
                    (b"x-process-time-ms", f"{process_ms:.2f}".encode("ascii"))
                )
                headers.append(
                    (b"server-timing", f"app;dur={process_ms:.2f}".encode("ascii"))
                )
                message["headers"] = headers
            await send(message)

        logger.info("request_started", method=method, path=path)
        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            latency_ms = round((time.perf_counter() - started_at) * 1000, 2)
            logger.info(
                "request_completed",
                method=method,
                path=path,
                status_code=status_code,
                latency_ms=latency_ms,
            )
            clear_contextvars()
