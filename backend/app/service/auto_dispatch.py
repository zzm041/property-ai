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
    DispatchWeights,
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
# docs/05 requires a scoped STAFF role *or above*. Supervisors and property /
# tenant administrators are therefore eligible when every other live employee
# filter passes; this does not bypass scope, attendance, capability, or load.
_STAFF_ROLES = frozenset({"STAFF", "SUPERVISOR", "PROPERTY_ADMIN", "TENANT_ADMIN"})


def configured_dispatch_weights(settings: Settings) -> DispatchWeights:
    return DispatchWeights(
        duty=settings.dispatch_weight_duty,
        skill=settings.dispatch_weight_skill,
        capacity=settings.dispatch_weight_capacity,
        same_building=settings.dispatch_weight_same_building,
        response=settings.dispatch_weight_response,
        completion=settings.dispatch_weight_completion,
        rating=settings.dispatch_weight_rating,
        response_reference_seconds=settings.dispatch_response_reference_seconds,
    )


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
        ranked, filter_snapshot = self._evaluate_live_candidates(record, mapped)
        if not ranked:
            return "NO_ELIGIBLE"
        for winner, score, reasons in ranked:
            nested = self._session.begin_nested()
            try:
                outcome = self._commit_auto_assignment(
                    record=record,
                    mapped=mapped,
                    winner=winner,
                    score=score,
                    reasons=reasons,
                    filter_snapshot=filter_snapshot,
                    request_id=request_id,
                )
                nested.commit()
                if outcome == "ASSIGNED":
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
        return "NO_ELIGIBLE"

    def _evaluate_live_candidates(
        self,
        record: WorkOrder,
        mapped: MappedAnalysis,
    ) -> tuple[
        tuple[tuple[DispatchCandidate, float, tuple[str, ...]], ...],
        tuple[dict[str, Any], ...],
    ]:
        assert self._ai is not None and self._work_orders is not None
        now = utc_now()
        property_record = self._work_orders.get_property(record.property_id)
        if property_record is None:
            return (), ()
        business_date = current_business_date(property_record.timezone, clock=utc_now)
        building_id = self._ai.get_building_id(record.house_id)
        employees = self._ai.list_property_employees(
            tenant_id=record.tenant_id,
            property_id=record.property_id,
        )
        incident = self._ai.get_incident(record.incident_id) if record.incident_id else None
        primary_employee_id = None if incident is None else incident.primary_employee_id
        candidates: list[DispatchCandidate] = []
        filter_reasons: dict[int, str | None] = {}
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
            attendance = self._ai.get_attendance(
                employee_id=employee.id,
                property_id=record.property_id,
                business_date=business_date,
            )
            roles = (
                self._ai.list_employee_role_codes(
                    employee.user_id,
                    tenant_id=record.tenant_id,
                    property_id=record.property_id,
                )
                if employee.user_id
                else frozenset()
            )
            response, completion, rating = self._ai.get_dispatch_history(
                employee_id=employee.id,
                tenant_id=record.tenant_id,
                property_id=record.property_id,
            )
            candidate = DispatchCandidate(
                employee_id=employee.id,
                duty_codes=self._ai.list_employee_duties(employee.id),
                skill_codes=self._ai.list_employee_skills(employee.id),
                current_load=0 if daily is None else daily.current_load,
                max_capacity=employee.max_capacity,
                on_duty=daily is not None
                and daily.on_duty == AttendanceOnDuty.ON_DUTY.value,
                employee_active=employee.status == EmployeeStatus.ACTIVE.value,
                user_active=self._ai.get_user_status(employee.user_id) == "ACTIVE",
                property_effective=assignment is not None,
                has_staff_role=bool(roles & _STAFF_ROLES),
                checked_in=attendance is not None,
                check_in_at=None if attendance is None else attendance.check_in_at,
                same_building_history=self._ai.count_same_building_history(
                    employee_id=employee.id,
                    building_id=building_id,
                    tenant_id=record.tenant_id,
                    property_id=record.property_id,
                ),
                primary_incident_handler=employee.id == primary_employee_id,
                average_response_seconds=response,
                completion_rate=completion,
                average_rating=rating,
            )
            reason = hard_filter_candidate(
                candidate,
                required_skill=mapped.required_skill,
                required_duty=mapped.required_duty,
            )
            candidates.append(candidate)
            filter_reasons[candidate.employee_id] = None if reason is None else reason.value
        ranked = rank_candidates(
            tuple(candidates),
            required_skill=mapped.required_skill,
            required_duty=mapped.required_duty,
            weights=configured_dispatch_weights(self._settings),
        )
        ranking = {
            candidate.employee_id: (rank_no, score, reasons)
            for rank_no, (candidate, score, reasons) in enumerate(ranked, start=1)
        }
        snapshot: list[dict[str, Any]] = []
        for candidate in candidates:
            rank_no, score, reasons = ranking.get(candidate.employee_id, (None, 0.0, ()))
            snapshot.append(
                {
                    "employee_id": candidate.employee_id,
                    "eligible": filter_reasons[candidate.employee_id] is None,
                    "filter_reason": filter_reasons[candidate.employee_id],
                    "rank": rank_no,
                    "score": score,
                    "reasons": list(reasons),
                    "employee_active": candidate.employee_active,
                    "user_active": candidate.user_active,
                    "property_effective": candidate.property_effective,
                    "checked_in": candidate.checked_in,
                    "on_duty": candidate.on_duty,
                    "has_staff_role": candidate.has_staff_role,
                    "duty_codes": sorted(candidate.duty_codes),
                    "skill_codes": sorted(candidate.skill_codes),
                    "current_load": candidate.current_load,
                    "max_capacity": candidate.max_capacity,
                    "check_in_at": (
                        None
                        if candidate.check_in_at is None
                        else candidate.check_in_at.isoformat()
                    ),
                    "same_building_history": candidate.same_building_history,
                    "primary_incident_handler": candidate.primary_incident_handler,
                    "average_response_seconds": candidate.average_response_seconds,
                    "completion_rate": candidate.completion_rate,
                    "average_rating": candidate.average_rating,
                }
            )
        return ranked, tuple(snapshot)

    def _commit_auto_assignment(
        self,
        *,
        record: WorkOrder,
        mapped: MappedAnalysis,
        winner: DispatchCandidate,
        score: float,
        reasons: tuple[str, ...],
        filter_snapshot: tuple[dict[str, Any], ...],
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
        attendance = self._ai.get_attendance(
            employee_id=employee.id,
            property_id=record.property_id,
            business_date=business_date,
        )
        assignment = self._ai.get_effective_property(
            employee_id=employee.id,
            property_id=record.property_id,
            now=utc_now(),
        )
        roles = (
            self._ai.list_employee_role_codes(
                employee.user_id,
                tenant_id=record.tenant_id,
                property_id=record.property_id,
            )
            if employee.user_id
            else frozenset()
        )
        current_candidate = DispatchCandidate(
            employee_id=employee.id,
            duty_codes=self._ai.list_employee_duties(employee.id),
            skill_codes=self._ai.list_employee_skills(employee.id),
            current_load=daily.current_load,
            max_capacity=employee.max_capacity,
            on_duty=daily.on_duty == AttendanceOnDuty.ON_DUTY.value,
            employee_active=employee.status == EmployeeStatus.ACTIVE.value,
            user_active=self._ai.get_user_status(employee.user_id) == "ACTIVE",
            property_effective=assignment is not None,
            has_staff_role=bool(roles & _STAFF_ROLES),
            checked_in=attendance is not None,
            check_in_at=None if attendance is None else attendance.check_in_at,
        )
        if hard_filter_candidate(
            current_candidate,
            required_skill=mapped.required_skill,
            required_duty=mapped.required_duty,
        ) is not None:
            return "NO_ELIGIBLE"
        now = utc_now()
        weights = configured_dispatch_weights(self._settings)
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
            "filter_snapshot": {
                "weights": {
                    "duty": weights.duty,
                    "skill": weights.skill,
                    "capacity": weights.capacity,
                    "same_building": weights.same_building,
                    "response": weights.response,
                    "completion": weights.completion,
                    "rating": weights.rating,
                    "response_reference_seconds": weights.response_reference_seconds,
                },
                "candidates": list(filter_snapshot),
            },
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
        if record.incident_id is not None:
            incident = self._ai.get_incident(record.incident_id)
            if (
                incident is not None
                and incident.primary_work_order_id == record.id
                and incident.primary_employee_id is None
            ):
                incident.primary_employee_id = employee.id
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
