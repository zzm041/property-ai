"""File metadata registration and authorized content upload."""

from typing import Annotated

from fastapi import APIRouter, Depends, File, Request, UploadFile

from app.api.dependencies.auth import get_current_principal
from app.api.v1.work_orders import get_work_order_service
from app.core.errors import AppError, FileErrorCode
from app.domain.iam import Principal
from app.schema.common import request_id_for, success_envelope
from app.schema.work_order import FileObjectData, RegisterFileRequest
from app.service.work_order import WorkOrderService

router = APIRouter(tags=["files"])


def _file_data(record) -> FileObjectData:
    return FileObjectData(
        id=record.id,
        tenant_id=record.tenant_id,
        property_id=record.property_id,
        uploader_user_id=record.uploader_user_id,
        mime_type=record.mime_type,
        size_bytes=record.size_bytes,
        storage_key=record.storage_key,
        scanner_status=record.scanner_status,
        created_at=record.created_at,
        upload_url=getattr(record, "upload_url", None),
        upload_expires_in=getattr(record, "upload_expires_in", None),
    )


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
    return success_envelope(request, _file_data(record).model_dump(mode="json"))


@router.post("/files/{file_id}/content")
def upload_file_content(
    file_id: int,
    request: Request,
    actor: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[WorkOrderService, Depends(get_work_order_service)],
    file: Annotated[UploadFile, File()],
) -> dict[str, object]:
    content = file.file.read()
    if content is None:
        raise AppError(
            code=FileErrorCode.INVALID_INPUT,
            message="File content is required",
            status_code=422,
        )
    record = service.put_file_content(
        actor=actor,
        file_id=file_id,
        content=content,
        request_id=request_id_for(request),
    )
    return success_envelope(request, _file_data(record).model_dump(mode="json"))
