"""Knowledge assistant query."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.api.dependencies.auth import get_current_principal
from app.api.dependencies.db import get_db_session
from app.core.config import Settings, get_settings
from app.domain.iam import Principal
from app.schema.common import request_id_for, success_envelope
from app.schema.knowledge import (
    AssistantAnswerData,
    AssistantQueryRequest,
    KnowledgeCitationData,
)
from app.service.retrieval import RetrievalService

router = APIRouter(prefix="/assistant", tags=["assistant"])


def get_retrieval_service(
    session: Annotated[Session, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> RetrievalService:
    return RetrievalService(session, settings=settings)


@router.post("/query")
def query_assistant(
    body: AssistantQueryRequest,
    request: Request,
    actor: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[RetrievalService, Depends(get_retrieval_service)],
) -> dict[str, object]:
    result = service.query(
        actor=actor,
        question=body.question,
        request_id=request_id_for(request),
        property_id=body.property_id,
        claimed_tenant_id=body.tenant_id,
        audience=body.audience,
    )
    data = AssistantAnswerData(
        status=result.status,
        answer=result.answer,
        citations=[
            KnowledgeCitationData.model_validate(item) for item in result.citations
        ],
    )
    return success_envelope(request, data.model_dump(mode="json"))
