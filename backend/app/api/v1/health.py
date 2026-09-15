"""Process liveness and dependency readiness endpoints."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.core.config import Settings, get_settings
from app.core.readiness import check_readiness
from app.schema.common import success_envelope

router = APIRouter(tags=["system"])


@router.get("/healthz")
def healthz(request: Request) -> dict[str, object]:
    """Liveness only: no external dependency calls."""

    return success_envelope(request, {"status": "ok"})


@router.get("/readyz")
def readyz(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> JSONResponse:
    """MySQL is critical; Redis and RabbitMQ may degrade optional capabilities."""

    result, status_code = check_readiness(settings)
    return JSONResponse(
        status_code=status_code,
        content=success_envelope(request, result),
    )
