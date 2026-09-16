"""Supervisor AI analysis and dispatch recommendation endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.api.dependencies.auth import get_current_principal
from app.api.dependencies.db import get_db_session
from app.api.v1.work_orders import work_order_data
from app.domain.iam import Principal
from app.schema.ai import (
    DispatchRecommendationData,
    DispatchRecommendationListData,
    WorkOrderAnalysisData,
)
from app.schema.common import request_id_for, success_envelope
from app.service.analysis import AnalysisService

router = APIRouter(prefix="/supervisor", tags=["supervisor-ai"])


def get_analysis_service(
    session: Annotated[Session, Depends(get_db_session)],
) -> AnalysisService:
    return AnalysisService(session)


def analysis_data(record) -> WorkOrderAnalysisData:
    return WorkOrderAnalysisData(
        id=record.id,
        work_order_id=record.work_order_id,
        analysis_version=record.analysis_version,
        status=record.status,
        category=record.category,
        subcategory=record.subcategory,
        severity=record.severity,
        priority=record.priority,
        summary=record.summary,
        risk=record.risk,
        required_role=record.required_role,
        required_skill=record.required_skill,
        confidence=record.confidence,
        error_code=record.error_code,
    )


@router.post("/work-orders/{work_order_id}/analyze")
def analyze_work_order(
    work_order_id: int,
    request: Request,
    actor: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[AnalysisService, Depends(get_analysis_service)],
) -> dict[str, object]:
    record = service.analyze_work_order(
        work_order_id=work_order_id,
        request_id=request_id_for(request),
        actor=actor,
    )
    work_order = service._work_orders.get_work_order(work_order_id)
    return success_envelope(
        request,
        {
            "analysis": None if record is None else analysis_data(record).model_dump(mode="json"),
            "work_order": None if work_order is None else work_order_data(work_order).model_dump(mode="json"),
        },
    )


@router.get("/work-orders/{work_order_id}/analysis")
def get_work_order_analysis(
    work_order_id: int,
    request: Request,
    actor: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[AnalysisService, Depends(get_analysis_service)],
) -> dict[str, object]:
    record = service.get_analysis(actor=actor, work_order_id=work_order_id)
    return success_envelope(
        request,
        None if record is None else analysis_data(record).model_dump(mode="json"),
    )


@router.get("/work-orders/{work_order_id}/recommendations")
def list_dispatch_recommendations(
    work_order_id: int,
    request: Request,
    actor: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[AnalysisService, Depends(get_analysis_service)],
) -> dict[str, object]:
    items = [
        DispatchRecommendationData(
            employee_id=item.employee_id,
            score=item.score,
            rank_no=item.rank_no,
            eligible=bool(item.eligible),
            reason_codes=list(item.reason_codes or ()),
            filter_reason=item.filter_reason,
        )
        for item in service.list_recommendations(actor=actor, work_order_id=work_order_id)
    ]
    return success_envelope(
        request,
        DispatchRecommendationListData(items=items, next_cursor=None).model_dump(mode="json"),
    )
