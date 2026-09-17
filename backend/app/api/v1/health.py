"""Process liveness and dependency readiness endpoints."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.api.dependencies.auth import get_current_principal
from app.core.config import Settings, get_settings
from app.core.errors import AppError, IamErrorCode
from app.core.readiness import check_readiness
from app.domain.iam import Principal
from app.schema.common import success_envelope

router = APIRouter(tags=["system"])

_OPSZ_PERMISSIONS = frozenset(
    {
        "role:read",
        "audit:read",
        "work_order:dispatch",
        "knowledge:manage",
        "employee:manage",
    }
)


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


@router.get("/opsz")
def opsz(
    request: Request,
    principal: Principal = Depends(get_current_principal),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    """Operator namespaces. Requires an operator permission; never includes secrets."""

    if not any(permission in principal.permissions for permission in _OPSZ_PERMISSIONS):
        raise AppError(
            code=IamErrorCode.FORBIDDEN,
            message="Operator status is not visible to this principal",
            status_code=403,
        )
    return success_envelope(
        request,
        {
            "app_env": settings.app_env,
            "celery_queue": settings.resolved_celery_queue,
            "celery_dlq": settings.resolved_celery_dlq,
            "milvus_collection": settings.milvus_collection,
            "storage_provider": settings.storage_provider,
            "storage_bucket": settings.storage_bucket or None,
            "llm_provider": settings.llm_provider,
        },
    )
