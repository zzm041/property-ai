"""File metadata registration. Development stores opaque keys, not paths."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from app.api.dependencies.auth import get_current_principal
from app.api.v1.work_orders import get_work_order_service
from app.domain.iam import Principal
from app.schema.common import request_id_for, success_envelope
from app.schema.work_order import FileObjectData, RegisterFileRequest
from app.service.work_order import WorkOrderService

router = APIRouter(tags=["files"])


@router.post("/files")
def register_file(
    body: RegisterFileRequest,
    request: Request,
    actor: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[WorkOrderService, Depends(get_work_order_service)],
) -> dict[str, object]:
    record = service.register_file(
        actor=actor,
        mime_type=body.mime_type,
        size_bytes=body.size_bytes,
        request_id=request_id_for(request),
        original_name=body.original_name,
        claimed_tenant_id=body.tenant_id,
        claimed_property_id=body.property_id,
    )
    return success_envelope(
        request,
        FileObjectData(
            id=record.id,
            tenant_id=record.tenant_id,
            property_id=record.property_id,
            uploader_user_id=record.uploader_user_id,
            mime_type=record.mime_type,
            size_bytes=record.size_bytes,
            storage_key=record.storage_key,
            scanner_status=record.scanner_status,
            created_at=record.created_at,
        ).model_dump(mode="json"),
    )
