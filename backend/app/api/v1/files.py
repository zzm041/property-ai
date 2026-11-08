"""File metadata registration and authorized content upload."""

from typing import Annotated, BinaryIO, Literal

from fastapi import APIRouter, Depends, File, Request, Response, UploadFile

from app.api.dependencies.auth import get_current_principal
from app.api.v1.work_orders import get_work_order_service
from app.core.errors import AppError, FileErrorCode
from app.domain.iam import Principal
from app.schema.common import request_id_for, success_envelope
from app.schema.work_order import FileObjectData, RegisterFileRequest
from app.service.work_order import WorkOrderService

router = APIRouter(tags=["files"])


def _read_upload_content(file: BinaryIO, *, max_bytes: int) -> bytes:
    """Read one byte beyond the limit so oversized bodies never fill memory."""

    content = file.read(max_bytes + 1)
    if len(content) > max_bytes:
        raise AppError(
            code=FileErrorCode.TOO_LARGE,
            message="File exceeds the upload size limit",
            status_code=422,
        )
    return content


def _file_data(record) -> FileObjectData:
    return FileObjectData(
        id=record.id,
        tenant_id=record.tenant_id,
        property_id=record.property_id,
        uploader_user_id=record.uploader_user_id,
        mime_type=record.mime_type,
        size_bytes=record.size_bytes,
        created_at=record.created_at,
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
    content = _read_upload_content(
        file.file,
        max_bytes=service.max_upload_bytes,
    )
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


@router.get("/files/{file_id}/content")
def download_file_content(
    file_id: int,
    actor: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[WorkOrderService, Depends(get_work_order_service)],
    audience: Literal["resident", "property"] = "resident",
) -> Response:
    record, content = service.get_file_content(actor=actor, file_id=file_id, audience=audience)
    return Response(
        content=content,
        media_type=record.mime_type,
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
