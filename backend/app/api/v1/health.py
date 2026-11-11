"""Process liveness and dependency readiness endpoints."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.api.dependencies.auth import get_current_principal
from app.core.buildinfo import build_info
from app.core.config import Settings, get_settings
from app.core.readiness import check_readiness, queue_depth
from app.domain.identity import OrganizationScope, Principal, ScopeType
from app.schema.common import success_envelope
from app.service.identity.rbac import RbacService

router = APIRouter(tags=["system"])

_OPSZ_PERMISSION = "audit:read"


@router.get("/healthz")
def healthz(request: Request) -> dict[str, object]:
    """Liveness only: no external dependency calls.

    ``build`` is static provenance (baked source commit, resolved package path,
    contract file digests) so PA-116 drift is visible without exec'ing a shell.
    """

    return success_envelope(request, {"status": "ok", "build": build_info()})


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
    """Operator namespaces. SYSTEM-scoped audit:read only; never includes secrets."""

    RbacService.require_permission(
        principal,
        _OPSZ_PERMISSION,
        OrganizationScope(ScopeType.SYSTEM),
    )
    return success_envelope(
        request,
        {
            "app_env": settings.app_env,
            "celery_queue": settings.resolved_celery_queue,
            "celery_dlq": settings.resolved_celery_dlq,
            # PA-104-D: backlog must be visible, not inferred.
            "celery_queue_depth": queue_depth(settings),
            "milvus_collection": settings.milvus_collection,
            "storage_provider": settings.storage_provider,
            "storage_bucket": settings.storage_bucket or None,
            "llm_provider": settings.llm_provider,
        },
    )
