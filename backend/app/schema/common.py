"""Shared API response envelopes."""

from typing import Any, Generic, TypeVar

from fastapi import Request
from pydantic import BaseModel, Field

DataT = TypeVar("DataT")


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ErrorEnvelope(BaseModel):
    request_id: str
    error: ErrorDetail


class SuccessEnvelope(BaseModel, Generic[DataT]):
    """The frozen success shape, typed so OpenAPI can show ``data``."""

    request_id: str
    data: DataT


def request_id_for(request: Request) -> str:
    """Read the ID established by RequestContextMiddleware."""

    return str(getattr(request.state, "request_id", "unknown"))


def success_envelope(request: Request, data: Any) -> dict[str, Any]:
    """Build the frozen successful response shape."""

    return {"request_id": request_id_for(request), "data": data}
