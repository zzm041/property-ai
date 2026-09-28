"""Automatic assignment after validated analysis. Does not let the LLM pick people."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import MetaData, Table, inspect as sa_inspect, insert
from sqlalchemy.exc import IntegrityError, OperationalError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.logging import get_logger
from app.core.time import current_business_date, utc_now
from app.domain.ai import (
    AiRisk,
    DispatchCandidate,
    DispatchWeights,
    FilterReason,
    MappedAnalysis,
    hard_filter_candidate,
    rank_candidates,
    ordinary_maintenance,
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
from app.service.sla import reset_assignment_sla_deadlines

logger = get_logger(__name__)

# Ordinary skilled repair (electric / flooding / other / none) may auto-assign.
# SAFETY and GAS stay human fallback (spec: dangerous work needs a decision).
HUMAN_FALLBACK_RISKS = frozenset({AiRisk.SAFETY, AiRisk.GAS})
AUTO_ASSIGN_SCHEMA_COLUMNS = frozenset({"assigned_source", "payload_json"})
# docs/05 requires a scoped STAFF role *or above*. Supervisors and property /
# tenant administrators are therefore eligible when every other live employee
# filter passes; this does not bypass scope, attendance, capability, or load.
_STAFF_ROLES = frozenset({"STAFF", "SUPERVISOR", "PROPERTY_ADMIN", "TENANT_ADMIN"})


def _notification_intent_snapshot(session: Session) -> tuple[list[int], bool, object]:
    pending = list(session.info.get("pending_notification_deliveries", ()))
    has_settings = "notification_settings" in session.info
    return pending, has_settings, session.info.get("notification_settings")


def _restore_notification_intents(
    session: Session,
    snapshot: tuple[list[int], bool, object],
) -> None:
    pending, has_settings, settings = snapshot
    if pending:
        session.info["pending_notification_deliveries"] = pending
    else:
        session.info.pop("pending_notification_deliveries", None)
    if has_settings:
        session.info["notification_settings"] = settings
    else:
        session.info.pop("notification_settings", None)


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
            self._record_wait(record, filter_snapshot, request_id)
            return "NO_ELIGIBLE"
        for winner, score, reasons in ranked:
            notification_snapshot = _notification_intent_snapshot(self._session)
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
                _restore_notification_intents(
                    self._session,
                    notification_snapshot,
                )
                logger.warning(
                    "auto_dispatch_conflict",
                    work_order_id=record.id,
                    request_id=request_id,
                )
                return "ALREADY_ASSIGNED"
            except OperationalError:
                # A disconnect/deadlock invalidates the outer operation, not
                # just this candidate. Its owner must discard the transaction.
                _restore_notification_intents(self._session, notification_snapshot)
                raise
            except Exception:
                nested.rollback()
                _restore_notification_intents(
                    self._session,
                    notification_snapshot,
                )
                logger.exception(
                    "auto_dispatch_failed",
                    work_order_id=record.id,
                    request_id=request_id,
                )
                return "ASSIGN_FAILED"
        # The transactional recheck may have found a new reservation. Refresh
        # the reasons instead of presenting the initial idle snapshot as truth.
        _, refreshed_snapshot = self._evaluate_live_candidates(record, mapped)
        self._record_wait(record, refreshed_snapshot, request_id)
        return "NO_ELIGIBLE"

    def _record_wait(self, record: WorkOrder, snapshot: tuple, request_id: str, *, reason_code: str | None = None) -> None:
        """Durable scheduling event, not a terminal failure or phone storage."""
        if self._work_orders.has_dispatch_wait_attempt(work_order_id=record.id, request_id=request_id):
            return
        reasons = {item.get("filter_reason") for item in snapshot}
        reason = reason_code or ("WAITING_IDLE" if reasons & {"BUSY", "CAPACITY_FULL"} else "WAITING_ELIGIBLE")
        self._work_orders.add_log(WorkOrderLog(
            tenant_id=record.tenant_id, property_id=record.property_id,
            work_order_id=record.id, actor_user_id=None, actor_type="SYSTEM",
            from_status=record.status, to_status=record.status,
            process_reason=record.process_reason, event_code="AUTO_DISPATCH_WAIT",
            request_id=request_id,
            payload_json={"reason_code": reason, "policy_version": "maintenance-group-v1"},
        ))

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
        recently_rejected = self._recently_rejected_employee_ids(record)
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
                current_load=self._work_orders.employee_global_occupancy(
                    employee_id=employee.id, tenant_id=record.tenant_id,
                ) if ordinary_maintenance(mapped.required_duty, mapped.required_skill) else (0 if daily is None else daily.current_load),
                last_assignment_id=self._work_orders.employee_last_assignment_id(
                    employee_id=employee.id, tenant_id=record.tenant_id,
                ),
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
                incident_follow=(
                    incident is not None
                    and incident.primary_work_order_id != record.id
                    and employee.id == primary_employee_id
                ),
                average_response_seconds=response,
                completion_rate=completion,
                average_rating=rating,
            )
            reason = (
                FilterReason.RECENTLY_REJECTED
                if candidate.employee_id in recently_rejected
                else hard_filter_candidate(
                    candidate,
                    required_skill=mapped.required_skill,
                    required_duty=mapped.required_duty,
                )
            )
            candidates.append(candidate)
            filter_reasons[candidate.employee_id] = None if reason is None else reason.value
        ranked = rank_candidates(
            tuple(
                candidate
                for candidate in candidates
                if filter_reasons[candidate.employee_id] is None
            ),
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
                    "last_assignment_id": candidate.last_assignment_id,
                    "check_in_at": (
                        None
                        if candidate.check_in_at is None
                        else candidate.check_in_at.isoformat()
                    ),
                    "same_building_history": candidate.same_building_history,
                    "primary_incident_handler": candidate.primary_incident_handler,
                    "incident_follow": candidate.incident_follow,
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
        prior_assignments = self._work_orders.list_assignments(record.id)
        if winner.employee_id in self._recently_rejected_employee_ids(
            record,
            assignments=prior_assignments,
        ):
            return "NO_ELIGIBLE"
        employee = self._employees.get_employee_for_update(winner.employee_id)
        if employee is None or employee.tenant_id != record.tenant_id or employee.status != EmployeeStatus.ACTIVE.value:
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
        incident = (
            self._ai.get_incident_for_update(record.incident_id)
            if record.incident_id
            else None
        )
        incident_follow = (
            incident is not None
            and incident.primary_work_order_id != record.id
            and incident.primary_employee_id == employee.id
        )
        load = self._work_orders.employee_global_occupancy(
            employee_id=employee.id, tenant_id=record.tenant_id, lock=True,
        ) if ordinary_maintenance(mapped.required_duty, mapped.required_skill) else daily.current_load
        if load >= employee.max_capacity and not incident_follow:
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
            current_load=load,
            max_capacity=employee.max_capacity,
            on_duty=daily.on_duty == AttendanceOnDuty.ON_DUTY.value,
            employee_active=employee.status == EmployeeStatus.ACTIVE.value,
            user_active=self._ai.get_user_status(employee.user_id) == "ACTIVE",
            property_effective=assignment is not None,
            has_staff_role=bool(roles & _STAFF_ROLES),
            checked_in=attendance is not None,
            check_in_at=None if attendance is None else attendance.check_in_at,
            primary_incident_handler=incident is not None
            and incident.primary_employee_id == employee.id,
            incident_follow=incident_follow,
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
            "raw_required_skill": mapped.raw_required_skill or mapped.required_skill,
            "dispatch_contract_version": mapped.dispatch_contract_version,
            "skill_mapping_applied": mapped.skill_mapping_applied,
            "event_contract": "observed-event-v2-source-id" if mapped.subcategory.startswith('evt2:') else "legacy",
            "required_duty": mapped.required_duty,
            "dispatch_policy": "maintenance-group-v1",
            "ordinary_maintenance": ordinary_maintenance(mapped.required_duty, mapped.required_skill),
            "confidence": mapped.confidence,
            "risk": mapped.risk.value,
            "incident_id": None if incident is None else incident.id,
            "incident_follow": incident_follow,
            "incident_capacity_owner": incident is not None and not incident_follow,
            "current_load": load,
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
        reset_assignment_sla_deadlines(
            self._work_orders,
            work_order=record,
            now=now,
        )
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
                    "reason_code": (
                        "AUTO_INCIDENT_FOLLOW"
                        if incident_follow
                        else "AUTO_ASSIGN"
                    ),
                    "assigned_source": "AUTO",
                    "employee_id": employee.id,
                    "incident_id": None if incident is None else incident.id,
                },
            )
        )
        if not incident_follow:
            daily.current_load = daily.current_load + 1
        if (
            incident is not None
            and not incident_follow
            and incident.primary_employee_id != employee.id
        ):
            if incident.primary_work_order_id is not None:
                previous = self._work_orders.get_active_assignment(
                    incident.primary_work_order_id
                )
                if previous is not None:
                    self._work_orders.mark_auto_incident_capacity_owner(
                        previous.id,
                        incident_id=incident.id,
                    )
            incident.primary_work_order_id = record.id
            incident.primary_employee_id = employee.id
        self._session.flush()
        for item in self._ai.list_recommendations(record.id):
            item.selected = 1 if item.employee_id == employee.id else 0
        audit_action = (
            AuditAction.WORK_ORDER_REASSIGNED
            if prior_assignments
            else AuditAction.WORK_ORDER_ASSIGNED
        )
        audit_details: dict[str, object] = {
            "work_order_id": record.id,
            "employee_id": employee.id,
            "status": record.status,
        }
        if prior_assignments:
            audit_details["reason_code"] = "AUTO_REASSIGN"
        self._audit.append(
            action=audit_action,
            resource_type=AuditResourceType.WORK_ORDER_ASSIGNMENT,
            actor_user_id=None,
            resource_id=assignment_id,
            request_id=request_id,
            scope_type=ScopeType.PROPERTY,
            tenant_id=record.tenant_id,
            property_id=record.property_id,
            details=audit_details,
        )
        for user_id in sorted(
            {
                int(user_id)
                for user_id in (employee.user_id, record.reporter_user_id)
                if user_id is not None
            }
        ):
            NotificationService(self._session, settings=self._settings).enqueue(
                tenant_id=record.tenant_id,
                property_id=record.property_id,
                user_id=user_id,
                title="工单已派单",
                body="工单已安排处理，请查看最新进度",
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

    def _recently_rejected_employee_ids(
        self,
        record: WorkOrder,
        *,
        assignments: tuple[object, ...] | None = None,
    ) -> frozenset[int]:
        assert self._work_orders is not None
        cooldown = self._settings.dispatch_reject_cooldown_seconds
        if cooldown <= 0:
            return frozenset()
        history = (
            self._work_orders.list_assignments(record.id)
            if assignments is None
            else assignments
        )
        cutoff = utc_now() - timedelta(seconds=cooldown)
        return frozenset(
            int(item.employee_id)
            for item in history
            if item.ended_at is not None
            and item.ended_at >= cutoff
            and (
                item.status == AssignmentStatus.REJECTED.value
                or (
                    item.status == AssignmentStatus.REASSIGNED.value
                    and getattr(item, "timeout_at", None) is not None
                )
            )
        )

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
            reason=(
                "AUTO_INCIDENT_FOLLOW"
                if payload.get("incident_follow") is True
                else "AUTO_DISPATCH"
            ),
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
