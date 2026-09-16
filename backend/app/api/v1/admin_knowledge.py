"""Admin knowledge document create/archive."""

from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from sqlalchemy.orm import Session

from app.api.dependencies.auth import get_current_principal
from app.api.dependencies.db import get_db_session
from app.core.config import Settings, get_settings
from app.domain.iam import Principal
from app.schema.common import request_id_for, success_envelope
from app.schema.knowledge import (
    ArchiveKnowledgeDocumentRequest,
    CreateKnowledgeDocumentRequest,
    KnowledgeDocumentData,
    KnowledgeIngestionData,
)
from app.service.knowledge import KnowledgeService
from app.worker.tasks import run_ingest_knowledge

router = APIRouter(prefix="/admin", tags=["admin-knowledge"])


def get_knowledge_service(
    session: Annotated[Session, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> KnowledgeService:
    return KnowledgeService(session, settings=settings)


@router.post("/knowledge-documents")
def create_knowledge_document(
    body: CreateKnowledgeDocumentRequest,
    request: Request,
    background: BackgroundTasks,
    actor: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[KnowledgeService, Depends(get_knowledge_service)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, object]:
    document, job = service.create_document(
        actor=actor,
        property_id=body.property_id,
        title=body.title,
        document_type=body.document_type,
        visibility=body.visibility,
        source_text=body.source_text,
        request_id=request_id_for(request),
        claimed_tenant_id=body.tenant_id,
    )
    background.add_task(
        run_ingest_knowledge,
        document.id,
        job.version,
        request_id_for(request),
        settings,
    )
    data = KnowledgeIngestionData(
        document_id=document.id,
        version=job.version,
        job_status=job.status,
        content_hash=job.content_hash,
    )
    return success_envelope(request, data.model_dump(mode="json"))


@router.post("/knowledge-documents/{document_id}/archive")
def archive_knowledge_document(
    document_id: int,
    body: ArchiveKnowledgeDocumentRequest,
    request: Request,
    actor: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[KnowledgeService, Depends(get_knowledge_service)],
) -> dict[str, object]:
    record = service.archive_document(
        actor=actor,
        document_id=document_id,
        request_id=request_id_for(request),
        claimed_tenant_id=body.tenant_id,
        claimed_property_id=body.property_id,
    )
    data = KnowledgeDocumentData(
        id=record.id,
        tenant_id=record.tenant_id,
        property_id=record.property_id,
        title=record.title,
        document_type=record.document_type,
        visibility=record.visibility,
        status=record.status,
        current_version=record.current_version,
        created_at=record.created_at,
    )
    return success_envelope(request, data.model_dump(mode="json"))
