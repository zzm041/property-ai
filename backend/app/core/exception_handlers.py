"""FastAPI exception handlers for the stable error envelope."""

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.errors import AppError, ErrorCode
from app.core.logging import get_logger
from app.schema.common import ErrorDetail, ErrorEnvelope, request_id_for

logger = get_logger(__name__)


def _error_response(
    request: Request,
    *,
    status_code: int,
    code: str,
    message: str,
    details: dict[str, Any] | None = None,
) -> JSONResponse:
    request_id = request_id_for(request)
    content = ErrorEnvelope(
        request_id=request_id,
        error=ErrorDetail(code=code, message=message, details=details or {}),
    ).model_dump(mode="json")
    return JSONResponse(
        status_code=status_code,
        content=content,
        headers={"X-Request-Id": request_id},
    )


async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    # PA-127 F3: the envelope already carried `details.reason`, but the log line
    # only had code/status, so operations could not tell why a dispatch was
    # refused without replaying the request. Log the reason and the detail keys
    # (never the whole payload) so the diagnosis survives in the log stream.
    details = exc.details or {}
    logger.warning(
        "application_error",
        error_code=exc.code,
        status_code=exc.status_code,
        error_reason=details.get("reason"),
        error_detail_keys=sorted(details),
    )
    return _error_response(
        request,
        status_code=exc.status_code,
        code=exc.code,
        message=exc.message,
        details=exc.details,
    )


async def validation_error_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    errors = [
        {
            "type": error["type"],
            "location": [str(part) for part in error["loc"]],
            "message": error["msg"],
        }
        for error in exc.errors()
    ]
    return _error_response(
        request,
        status_code=422,
        code=ErrorCode.VALIDATION_ERROR,
        message="Request validation failed",
        details={"errors": errors},
    )


async def http_error_handler(
    request: Request, exc: StarletteHTTPException
) -> JSONResponse:
    if exc.status_code == 404:
        code = ErrorCode.NOT_FOUND
        message = "Resource not found"
    elif exc.status_code == 405:
        code = ErrorCode.METHOD_NOT_ALLOWED
        message = "Method not allowed"
    else:
        code = ErrorCode.HTTP_ERROR
        message = "Request could not be completed"
    return _error_response(
        request,
        status_code=exc.status_code,
        code=code,
        message=message,
    )


async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.error(
        "unhandled_exception",
        error_code=ErrorCode.INTERNAL_ERROR,
        error_type=type(exc).__name__,
        exc_info=exc,
    )
    return _error_response(
        request,
        status_code=500,
        code=ErrorCode.INTERNAL_ERROR,
        message="Internal server error",
    )


def install_exception_handlers(app: FastAPI) -> None:
    """Install handlers in one place so tests and the application stay aligned."""

    app.add_exception_handler(AppError, app_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(
        RequestValidationError, validation_error_handler  # type: ignore[arg-type]
    )
    app.add_exception_handler(
        StarletteHTTPException, http_error_handler  # type: ignore[arg-type]
    )
    app.add_exception_handler(Exception, unhandled_error_handler)
