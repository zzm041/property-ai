"""Stable application errors exposed by the HTTP error contract."""

from typing import Any


class ErrorCode:
    """Task 1 system-level error codes."""

    VALIDATION_ERROR = "SYS.VALIDATION_ERROR"
    NOT_FOUND = "SYS.NOT_FOUND"
    METHOD_NOT_ALLOWED = "SYS.METHOD_NOT_ALLOWED"
    HTTP_ERROR = "SYS.HTTP_ERROR"
    INTERNAL_ERROR = "SYS.INTERNAL_ERROR"


class AuthErrorCode:
    MISSING_TOKEN = "AUTH.MISSING_TOKEN"
    INVALID_TOKEN = "AUTH.INVALID_TOKEN"
    TOKEN_EXPIRED = "AUTH.TOKEN_EXPIRED"
    INVALID_USER = "AUTH.INVALID_USER"
    USER_DISABLED = "AUTH.USER_DISABLED"
    DEV_AUTH_DISABLED = "AUTH.DEV_AUTH_DISABLED"


class IamErrorCode:
    FORBIDDEN = "IAM.FORBIDDEN"
    INVALID_SCOPE = "IAM.INVALID_SCOPE"
    USER_NOT_FOUND = "IAM.USER_NOT_FOUND"
    ROLE_NOT_FOUND = "IAM.ROLE_NOT_FOUND"
    PERMISSION_NOT_FOUND = "IAM.PERMISSION_NOT_FOUND"
    DUPLICATE_GRANT = "IAM.DUPLICATE_GRANT"
    GRANT_NOT_FOUND = "IAM.GRANT_NOT_FOUND"
    MUTATION_CONFLICT = "IAM.MUTATION_CONFLICT"


class AuditErrorCode:
    INVALID_EVENT = "IAM.AUDIT_INVALID_EVENT"
    INVALID_SCOPE = "IAM.AUDIT_INVALID_SCOPE"
    INVALID_DETAILS = "IAM.AUDIT_INVALID_DETAILS"


class ResidentErrorCode:
    INVALID_INPUT = "RES.INVALID_INPUT"
    HOUSE_NOT_FOUND = "RES.HOUSE_NOT_FOUND"
    BIND_REQUEST_NOT_FOUND = "RES.BIND_REQUEST_NOT_FOUND"
    DUPLICATE_PENDING = "RES.DUPLICATE_PENDING"
    DUPLICATE_ACTIVE_RELATION = "RES.DUPLICATE_ACTIVE_RELATION"
    ALREADY_PROCESSED = "RES.ALREADY_PROCESSED"
    HOUSE_NOT_AVAILABLE = "RES.HOUSE_NOT_AVAILABLE"
    CONFLICT = "RES.CONFLICT"


class DevSeedErrorCode:
    ENVIRONMENT_FORBIDDEN = "DEV_SEED.ENVIRONMENT_FORBIDDEN"
    LOCK_UNAVAILABLE = "DEV_SEED.LOCK_UNAVAILABLE"
    DATA_CONFLICT = "DEV_SEED.DATA_CONFLICT"
    FAILED = "DEV_SEED.FAILED"


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
