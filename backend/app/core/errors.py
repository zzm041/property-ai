"""Stable application errors exposed by the HTTP error contract."""

from typing import Any


class ErrorCode:
    """Task 1 system-level error codes."""

    VALIDATION_ERROR = "SYS.VALIDATION_ERROR"
    NOT_FOUND = "SYS.NOT_FOUND"
    METHOD_NOT_ALLOWED = "SYS.METHOD_NOT_ALLOWED"
    HTTP_ERROR = "SYS.HTTP_ERROR"
    INTERNAL_ERROR = "SYS.INTERNAL_ERROR"


class AppError(Exception):
    """Expected business/application error with a stable public code."""

    def __init__(
        self,
        *,
        code: str,
        message: str,
        status_code: int,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}
