"""Shared API response envelopes."""

from typing import Any

from fastapi import Request
from pydantic import BaseModel, Field


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ErrorEnvelope(BaseModel):
    request_id: str
    error: ErrorDetail


def request_id_for(request: Request) -> str:
    """Read the ID established by RequestContextMiddleware."""

    return str(getattr(request.state, "request_id", "unknown"))


def success_envelope(request: Request, data: Any) -> dict[str, Any]:
    """Build the frozen successful response shape."""

    return {"request_id": request_id_for(request), "data": data}
