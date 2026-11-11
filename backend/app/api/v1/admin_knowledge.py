"""Admin knowledge document management and ingestion."""

from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request
from sqlalchemy.orm import Session

from app.api.dependencies.auth import get_current_principal
from app.api.dependencies.db import get_db_session
from app.core.config import Settings, get_settings
from app.domain.identity import Principal
from app.schema.common import request_id_for, success_envelope
from app.schema.knowledge import (
    ArchiveKnowledgeDocumentRequest,
    CreateKnowledgeDocumentRequest,
    CreateKnowledgeVersionRequest,
    KnowledgeDocumentData,
    KnowledgeDocumentDetailData,
    KnowledgeDocumentListData,
    KnowledgeIngestionData,
    KnowledgeIngestionJobData,
    KnowledgeVersionData,
)
from app.service.knowledge import KnowledgeDetail, KnowledgeService
from app.worker.enqueue import enqueue_celery_task
from app.worker.tasks import ingest_knowledge_task, run_ingest_knowledge

router = APIRouter(prefix="/admin", tags=["admin-knowledge"])


def get_knowledge_service(
    session: Annotated[Session, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> KnowledgeService:
    return KnowledgeService(session, settings=settings)


def knowledge_document_data(detail: KnowledgeDetail) -> KnowledgeDocumentDetailData:
    document, versions, jobs = detail
    return KnowledgeDocumentDetailData(
        id=document.id,
        tenant_id=document.tenant_id,
        property_id=document.property_id,
        title=document.title,
        document_type=document.document_type,
        visibility=document.visibility,
        status=document.status,
        current_version=document.current_version,
        created_at=document.created_at,
        versions=[
            KnowledgeVersionData(
                version=item.version,
                status=item.status,
                content_hash=item.content_hash,
                created_at=item.created_at,
            )
            for item in versions
        ],
        ingestion_jobs=[
            KnowledgeIngestionJobData(
                version=item.version,
                status=item.status,
                error_code=item.error_code,
                attempt=item.attempt,
                started_at=item.started_at,
                finished_at=item.finished_at,
            )
            for item in jobs
        ],
    )


def enqueue_ingestion(
    *,
    settings: Settings,
    background: BackgroundTasks,
    document_id: int,
    version: int,
    request_id: str,
) -> None:
    if settings.durable_tasks_enabled:
        return  # Service committed a typed durable intent alongside this version.
    enqueue_celery_task(
        settings=settings,
        celery_task=ingest_knowledge_task,
        args=(document_id, version, request_id),
        event="knowledge_ingest_enqueue_failed",
        extra={"document_id": document_id, "version": version},
        background=background,
        in_process=run_ingest_knowledge,
        in_process_args=(document_id, version, request_id, settings),
    )


@router.get("/knowledge-documents")
def list_knowledge_documents(
    request: Request,
    actor: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[KnowledgeService, Depends(get_knowledge_service)],
    property_id: Annotated[int, Query(gt=0)],
    include_archived: bool = False,
    after_id: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> dict[str, object]:
    details = service.list_managed(
        actor=actor,
        property_id=property_id,
        include_archived=include_archived,
        after_id=after_id,
        limit=limit,
    )
    items = [knowledge_document_data(item) for item in details]
    next_cursor = items[-1].id if len(items) == limit else None
    return success_envelope(
        request,
        KnowledgeDocumentListData(
            items=items,
            next_cursor=next_cursor,
        ).model_dump(mode="json"),
    )


@router.get("/knowledge-documents/{document_id}")
def get_knowledge_document(
    document_id: int,
    request: Request,
    actor: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[KnowledgeService, Depends(get_knowledge_service)],
) -> dict[str, object]:
    detail = service.get_managed(actor=actor, document_id=document_id)
    return success_envelope(
        request,
        knowledge_document_data(detail).model_dump(mode="json"),
    )


@router.post("/knowledge-documents")
def create_knowledge_document(
    body: CreateKnowledgeDocumentRequest,
    request: Request,
    background: BackgroundTasks,
    actor: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[KnowledgeService, Depends(get_knowledge_service)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, object]:
    request_id = request_id_for(request)
    document, job = service.create_document(
        actor=actor,
        property_id=body.property_id,
        title=body.title,
        document_type=body.document_type,
        visibility=body.visibility,
        source_text=body.source_text,
        request_id=request_id,
        claimed_tenant_id=body.tenant_id,
    )
    enqueue_ingestion(
        settings=settings,
        background=background,
        document_id=document.id,
        version=job.version,
        request_id=request_id,
    )
    data = KnowledgeIngestionData(
        document_id=document.id,
        version=job.version,
        job_status=job.status,
        content_hash=job.content_hash,
    )
    return success_envelope(request, data.model_dump(mode="json"))


@router.post("/knowledge-documents/{document_id}/versions")
def create_knowledge_version(
    document_id: int,
    body: CreateKnowledgeVersionRequest,
    request: Request,
    background: BackgroundTasks,
    actor: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[KnowledgeService, Depends(get_knowledge_service)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, object]:
    request_id = request_id_for(request)
    version, job = service.create_version(
        actor=actor,
        document_id=document_id,
        source_text=body.source_text,
        request_id=request_id,
        claimed_tenant_id=body.tenant_id,
        claimed_property_id=body.property_id,
    )
    enqueue_ingestion(
        settings=settings,
        background=background,
        document_id=document_id,
        version=version.version,
        request_id=request_id,
    )
    data = KnowledgeIngestionData(
        document_id=document_id,
        version=version.version,
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
