"""Automatic assignment after validated analysis. Does not let the LLM pick people."""

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.domain.ai import AiRisk, MappedAnalysis
from app.model.work_order import WorkOrderAssignment


class AutoDispatchService:
    def __init__(self, session: Session, *, settings: Settings) -> None:
        self._session = session
        self._settings = settings

    def maybe_assign(
        self,
        *,
        work_order: object,
        mapped: MappedAnalysis,
        request_id: str,
    ) -> str:
        del request_id
        if not self._settings.ai_auto_assign:
            return "DISABLED"
        if mapped.low_confidence:
            return "LOW_CONFIDENCE"
        if mapped.risk is not AiRisk.NONE:
            return "HIGH_RISK"
        if not hasattr(WorkOrderAssignment, "assigned_source"):
            return "SCHEMA_PENDING_0009"
        return "READY_WAITING_SCHEMA"
