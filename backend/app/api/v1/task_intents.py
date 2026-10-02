"""Scoped exact-task inspection and explicit operator recovery; no bulk replay."""
from typing import Annotated, Literal
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session
from app.api.dependencies.auth import get_current_principal
from app.api.dependencies.db import get_db_session
from app.core.config import Settings, get_settings
from app.core.errors import AppError
from app.domain.iam import Principal
from app.schema.common import request_id_for, success_envelope
from app.service.task_intent import TaskIntentService

router=APIRouter(prefix="/admin/task-intents",tags=["task-recovery"])
class RetryIntentRequest(BaseModel):
    model_config=ConfigDict(extra="forbid")
    reason_code: Literal["DEPENDENCY_RECOVERED","OPERATOR_VERIFIED"]
class CompleteAnalysisRequest(BaseModel):
    model_config=ConfigDict(extra="forbid")
    reason_code: Literal["ATTACHMENT_COMPLETION"]
def get_service(session:Annotated[Session,Depends(get_db_session)],settings:Annotated[Settings,Depends(get_settings)]):
    if not settings.durable_tasks_enabled:
        raise AppError(code="TASK.NOT_ENABLED",message="Durable recovery not enabled",status_code=409)
    return TaskIntentService(session)
@router.get("/{intent_id}")
def inspect_task(intent_id:int,request:Request,actor:Annotated[Principal,Depends(get_current_principal)],service:Annotated[TaskIntentService,Depends(get_service)]):
    return success_envelope(request,service.inspect_managed(intent_id,actor))
@router.post("/{intent_id}/retry")
def retry_task(intent_id:int,body:RetryIntentRequest,request:Request,actor:Annotated[Principal,Depends(get_current_principal)],service:Annotated[TaskIntentService,Depends(get_service)]):
    return success_envelope(request,service.retry_managed(intent_id,actor=actor,reason_code=body.reason_code,request_id=request_id_for(request)))
@router.post("/{intent_id}/complete-analysis")
def complete_analysis_task(intent_id:int,body:CompleteAnalysisRequest,request:Request,actor:Annotated[Principal,Depends(get_current_principal)],service:Annotated[TaskIntentService,Depends(get_service)]):
    return success_envelope(request,service.complete_analysis_managed(intent_id,actor=actor,reason_code=body.reason_code,request_id=request_id_for(request)))
