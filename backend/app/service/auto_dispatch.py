"""Automatic assignment after validated analysis. Does not let the LLM pick people."""

from __future__ import annotations

from typing import Any

from sqlalchemy import MetaData, Table, inspect as sa_inspect, insert
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.logging import get_logger
from app.core.time import current_business_date, utc_now
from app.domain.ai import (
    AiRisk,
    DispatchCandidate,
    MappedAnalysis,
    hard_filter_candidate,
    rank_candidates,
)
from app.domain.audit import AuditAction, AuditResourceType
from app.domain.employee import AttendanceOnDuty, EmployeeStatus
from app.domain.iam import ScopeType
from app.domain.work_order import (
    AssignmentStatus,
    WorkOrderActorType,
    WorkOrderEvent,
    WorkOrderStatus,
    require_legal_transition,
)
from app.model.work_order import WorkOrder, WorkOrderLog
from app.repository.ai import AiRepository
from app.repository.employee import EmployeeRepository
from app.repository.work_order import WorkOrderRepository
from app.service.audit import AuditService
from app.service.notification import NotificationService

logger = get_logger(__name__)

# Ordinary skilled repair (electric / flooding / other / none) may auto-assign.
# SAFETY and GAS stay human fallback (spec: dangerous work needs a decision).
HUMAN_FALLBACK_RISKS = frozenset({AiRisk.SAFETY, AiRisk.GAS})
AUTO_ASSIGN_SCHEMA_COLUMNS = frozenset({"assigned_source", "payload_json"})
_STAFF_ROLES = frozenset({"STAFF", "SUPERVISOR", "PROPERTY_ADMIN", "TENANT_ADMIN"})


def assignment_schema_ready(session: Session | None) -> bool:
    """True only when the live database has 0009 AUTO assignment columns."""

    if session is None:
        return False
    bind = session.get_bind()
    if bind is None:
        return False
    try:
        names = {column["name"] for column in sa_inspect(bind).get_columns("work_order_assignments")}
    except SQLAlchemyError:
        return False
    except Exception:
        return False
    return AUTO_ASSIGN_SCHEMA_COLUMNS <= names


class AutoDispatchService:
    def __init__(self, session: Session, *, settings: Settings) -> None:
        self._session = session
        self._settings = settings
        self._ai = AiRepository(session) if session is not None else None
        self._work_orders = WorkOrderRepository(session) if session is not None else None
        self._employees = EmployeeRepository(session) if session is not None else None
        self._audit = AuditService(session) if session is not None else None

    def maybe_assign(
        self,
        *,
        work_order: object,
        mapped: MappedAnalysis,
        request_id: str,
    ) -> str:
        if not self._settings.ai_auto_assign:
            return "DISABLED"
        if mapped.low_confidence:
            return "LOW_CONFIDENCE"
        if mapped.risk in HUMAN_FALLBACK_RISKS:
            return "HIGH_RISK"
        if not assignment_schema_ready(self._session):
            return "SCHEMA_PENDING_0009"
        record = work_order
        if not isinstance(record, WorkOrder):
            return "STALE_STATUS"
        current_status = str(record.status)
        if record.current_assignment_id is not None or current_status == WorkOrderStatus.ASSIGNED.value:
            return "ALREADY_ASSIGNED"
        if current_status != WorkOrderStatus.PENDING_ASSIGNMENT.value:
            return "STALE_STATUS"
        assert self._work_orders is not None
        existing = self._work_orders.get_active_assignment(record.id)
        if existing is not None:
            return "ALREADY_ASSIGNED"
        ranked = self._rank_live_candidates(record, mapped)
        if not ranked:
            return "NO_ELIGIBLE"
        winner, score, reasons = ranked[0]
        nested = self._session.begin_nested()
        try:
            outcome = self._commit_auto_assignment(
                record=record,
                mapped=mapped,
                winner=winner,
                score=score,
                reasons=reasons,
                request_id=request_id,
            )
            nested.commit()
            return outcome
        except IntegrityError:
            nested.rollback()
            logger.warning(
                "auto_dispatch_conflict",
                work_order_id=record.id,
                request_id=request_id,
            )
            return "ALREADY_ASSIGNED"
        except Exception:
            nested.rollback()
            logger.exception(
                "auto_dispatch_failed",
                work_order_id=record.id,
                request_id=request_id,
            )
            return "ASSIGN_FAILED"

    def _rank_live_candidates(
        self,
        record: WorkOrder,
        mapped: MappedAnalysis,
    ) -> tuple[tuple[DispatchCandidate, float, tuple[str, ...]], ...]:
        assert self._ai is not None and self._work_orders is not None
        now = utc_now()
        property_record = self._work_orders.get_property(record.property_id)
        if property_record is None:
            return ()
        business_date = current_business_date(property_record.timezone, clock=utc_now)
        building_id = self._ai.get_building_id(record.house_id)
        employees = self._ai.list_property_employees(
            tenant_id=record.tenant_id,
            property_id=record.property_id,
        )
        eligible: list[DispatchCandidate] = []
        for employee in employees:
            assignment = self._ai.get_effective_property(
                employee_id=employee.id,
                property_id=record.property_id,
                now=now,
            )
            daily = self._ai.get_daily_status(
                employee_id=employee.id,
                property_id=record.property_id,
                business_date=business_date,
            )
            roles = self._ai.list_employee_role_codes(employee.user_id) if employee.user_id else frozenset()
            candidate = DispatchCandidate(
                employee_id=employee.id,
                duty_codes=self._ai.list_employee_duties(employee.id),
                skill_codes=self._ai.list_employee_skills(employee.id),
                current_load=0 if daily is None else daily.current_load,
                max_capacity=employee.max_capacity,
                on_duty=daily is not None
                and daily.on_duty == AttendanceOnDuty.ON_DUTY.value,
                employee_active=employee.status == EmployeeStatus.ACTIVE.value,
                property_effective=assignment is not None,
                has_staff_role=bool(roles & _STAFF_ROLES),
                same_building_history=self._ai.count_same_building_history(
                    employee_id=employee.id,
                    building_id=building_id,
                    tenant_id=record.tenant_id,
                    property_id=record.property_id,
                ),
                primary_incident_handler=False,
            )
            if hard_filter_candidate(
                candidate,
                required_skill=mapped.required_skill,
                required_duty=mapped.required_duty,
            ) is None:
                eligible.append(candidate)
        return rank_candidates(
            tuple(eligible),
            required_skill=mapped.required_skill,
            required_duty=mapped.required_duty,
        )

    def _commit_auto_assignment(
        self,
        *,
        record: WorkOrder,
        mapped: MappedAnalysis,
        winner: DispatchCandidate,
        score: float,
        reasons: tuple[str, ...],
        request_id: str,
    ) -> str:
        assert self._employees is not None and self._work_orders is not None
        assert self._ai is not None and self._audit is not None
        employee = self._employees.get_employee(winner.employee_id)
        if employee is None or employee.status != EmployeeStatus.ACTIVE.value:
            return "NO_ELIGIBLE"
        property_record = self._work_orders.get_property(record.property_id)
        if property_record is None:
            return "NO_ELIGIBLE"
        business_date = current_business_date(property_record.timezone, clock=utc_now)
        daily = self._employees.get_daily_status_for_update(
            employee_id=employee.id,
            property_id=record.property_id,
            business_date=business_date,
        )
        if daily is None or daily.on_duty != AttendanceOnDuty.ON_DUTY.value:
            return "NO_ELIGIBLE"
        if daily.current_load >= employee.max_capacity:
            return "NO_ELIGIBLE"
        now = utc_now()
        payload: dict[str, Any] = {
            "assigned_source": "AUTO",
            "employee_id": winner.employee_id,
            "score": score,
            "reasons": list(reasons),
            "category": mapped.category.value,
            "required_skill": mapped.required_skill,
            "required_duty": mapped.required_duty,
            "confidence": mapped.confidence,
            "risk": mapped.risk.value,
            "current_load": winner.current_load,
            "max_capacity": winner.max_capacity,
        }
        assignment_id = self._insert_auto_assignment(
            record,
            employee_id=employee.id,
            assigned_at=now,
            payload=payload,
        )
        from_status = WorkOrderStatus(record.status)
        to_status = WorkOrderStatus.ASSIGNED
        require_legal_transition(
            from_status=from_status,
            to_status=to_status,
            event=WorkOrderEvent.ASSIGN,
        )
        record.status = to_status.value
        record.version += 1
        record.current_assignment_id = assignment_id
        record.assigned_at = now
        record.accepted_at = None
        record.started_at = None
        record.resolved_at = None
        self._work_orders.add_log(
            WorkOrderLog(
                tenant_id=record.tenant_id,
                property_id=record.property_id,
                work_order_id=record.id,
                actor_user_id=None,
                actor_type=WorkOrderActorType.SYSTEM.value,
                from_status=from_status.value,
                to_status=to_status.value,
                process_reason=record.process_reason,
                event_code=WorkOrderEvent.ASSIGN.value,
                request_id=request_id,
                payload_json={
                    "event_code": WorkOrderEvent.ASSIGN.value,
                    "reason_code": "AUTO_ASSIGN",
                    "assigned_source": "AUTO",
                    "employee_id": employee.id,
                },
            )
        )
        daily.current_load = daily.current_load + 1
        self._session.flush()
        for item in self._ai.list_recommendations(record.id):
            if item.employee_id == employee.id:
                item.selected = 1
        self._audit.append(
            action=AuditAction.WORK_ORDER_ASSIGNED,
            resource_type=AuditResourceType.WORK_ORDER_ASSIGNMENT,
            actor_user_id=None,
            resource_id=assignment_id,
            request_id=request_id,
            scope_type=ScopeType.PROPERTY,
            tenant_id=record.tenant_id,
            property_id=record.property_id,
            details={
                "work_order_id": record.id,
                "employee_id": employee.id,
                "status": record.status,
            },
        )
        if employee.user_id:
            NotificationService(self._session, settings=self._settings).enqueue(
                tenant_id=record.tenant_id,
                property_id=record.property_id,
                user_id=employee.user_id,
                title="工单已派单",
                body="You have a new assigned work order",
                resource_type="WORK_ORDER",
                resource_id=record.id,
            )
        logger.info(
            "auto_dispatch_assigned",
            work_order_id=record.id,
            employee_id=employee.id,
            assignment_id=assignment_id,
            score=score,
            request_id=request_id,
        )
        return "ASSIGNED"

    def _insert_auto_assignment(
        self,
        record: WorkOrder,
        *,
        employee_id: int,
        assigned_at: object,
        payload: dict[str, Any],
    ) -> int:
        bind = self._session.get_bind()
        table = Table("work_order_assignments", MetaData(), autoload_with=bind)
        stmt = insert(table).values(
            tenant_id=record.tenant_id,
            property_id=record.property_id,
            work_order_id=record.id,
            employee_id=employee_id,
            assigned_by_user_id=None,
            assigned_source="AUTO",
            payload_json=payload,
            status=AssignmentStatus.ACTIVE.value,
            assigned_at=assigned_at,
            reason="AUTO_DISPATCH",
        )
        result = self._session.execute(stmt)
        self._session.flush()
        primary = result.inserted_primary_key
        if primary and primary[0] is not None:
            return int(primary[0])
        lastrowid = getattr(result, "lastrowid", None)
        if lastrowid:
            return int(lastrowid)
        raise RuntimeError("AUTO assignment insert did not return an id")
