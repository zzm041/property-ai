"""Idempotent SLA warning and overdue escalation scheduling."""

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.database import begin_owned_transaction
from app.core.time import current_business_date, utc_now
from app.domain.audit import AuditAction, AuditResourceType
from app.domain.iam import ScopeType
from app.domain.work_order import (
    AssignmentStatus,
    SlaClockState,
    TERMINAL_STATUSES,
    WorkOrderActorType,
    WorkOrderEvent,
    WorkOrderPriority,
    WorkOrderStatus,
    require_legal_transition,
    sla_windows_for_priority,
)
from app.model.work_order import WorkOrderLog
from app.repository.employee import EmployeeRepository
from app.repository.work_order import WorkOrderRepository
from app.service.audit import AuditService
from app.service.notification import NotificationService

_SLA_STOP_STATUSES = frozenset(
    {status.value for status in TERMINAL_STATUSES} | {"WAIT_CONFIRM"}
)


@dataclass(frozen=True, slots=True)
class SlaSweepResult:
    scanned: int = 0
    warnings: int = 0
    overdue: int = 0
    stopped: int = 0
    notifications: int = 0

    def as_dict(self) -> dict[str, int]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class AssignmentSlaSweepResult:
    scanned: int = 0
    accept_timeouts: int = 0
    response_timeouts: int = 0
    notifications: int = 0
    redispatch_enqueued: int = 0

    def as_dict(self) -> dict[str, int]:
        return asdict(self)


def _response_window_minutes(repository: WorkOrderRepository, work_order) -> int:
    priority = WorkOrderPriority(work_order.priority)
    respond_minutes, _ = sla_windows_for_priority(priority)
    policy = repository.find_sla_policy(
        tenant_id=work_order.tenant_id,
        property_id=work_order.property_id,
        category=work_order.category,
        priority=priority.value,
    )
    return respond_minutes if policy is None else int(policy.respond_minutes)


def reset_assignment_sla_deadlines(
    repository: WorkOrderRepository,
    *,
    work_order,
    now: datetime,
) -> None:
    """Start a fresh accept window whenever an assignment is created."""

    clock = repository.get_sla_clock_for_update(work_order.id)
    if clock is None:
        return
    deadline = now + timedelta(
        minutes=_response_window_minutes(repository, work_order)
    )
    clock.accept_due_at = deadline
    # The response window is restarted from the actual accept event below. Keep
    # a valid interim value for the non-null 0009 column.
    clock.respond_due_at = deadline


def reset_response_sla_deadline(
    repository: WorkOrderRepository,
    *,
    work_order,
    now: datetime,
) -> None:
    """Start the response window from staff acceptance, not order creation."""

    clock = repository.get_sla_clock_for_update(work_order.id)
    if clock is None:
        return
    clock.respond_due_at = now + timedelta(
        minutes=_response_window_minutes(repository, work_order)
    )


class SlaService:
    def __init__(
        self,
        session: Session,
        repository: WorkOrderRepository | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._session = session
        self._repository = repository or WorkOrderRepository(session)
        self._settings = settings or get_settings()
        self._employees = EmployeeRepository(session)
        self._audit = AuditService(session)

    def sweep_due(
        self,
        *,
        now: datetime | None = None,
        limit: int = 100,
        work_order_id: int | None = None,
    ) -> SlaSweepResult:
        effective_now = now or utc_now()
        bounded_limit = min(max(limit, 1), 500)
        counts = {
            "scanned": 0,
            "warnings": 0,
            "overdue": 0,
            "stopped": 0,
            "notifications": 0,
        }
        with begin_owned_transaction(self._session):
            notifier = NotificationService(self._session, settings=self._settings)
            clocks = self._repository.list_due_sla_clocks_for_update(
                now=effective_now,
                limit=bounded_limit,
                work_order_id=work_order_id,
            )
            for clock in clocks:
                counts["scanned"] += 1
                work_order = self._repository.get_work_order(clock.work_order_id)
                if work_order is None or work_order.status in _SLA_STOP_STATUSES:
                    clock.state = SlaClockState.STOPPED.value
                    counts["stopped"] += 1
                    continue

                target = self._target_state(clock=clock, now=effective_now)
                if target is None or target.value == clock.state:
                    continue
                clock.state = target.value
                if target is SlaClockState.WARNING:
                    counts["warnings"] += 1
                else:
                    counts["overdue"] += 1

                self._repository.add_log(
                    WorkOrderLog(
                        tenant_id=work_order.tenant_id,
                        property_id=work_order.property_id,
                        work_order_id=work_order.id,
                        actor_user_id=None,
                        actor_type="SYSTEM",
                        from_status=work_order.status,
                        to_status=work_order.status,
                        process_reason=work_order.process_reason,
                        event_code=(
                            WorkOrderEvent.SLA_WARNING.value
                            if target is SlaClockState.WARNING
                            else WorkOrderEvent.SLA_OVERDUE.value
                        ),
                        request_id=f"sla-clock:{clock.id}:{target.value.lower()}",
                        payload_json={
                            "sla_state": target.value,
                            "complete_due_at": clock.complete_due_at.isoformat(),
                        },
                    )
                )

                recipients = set(
                    self._repository.list_sla_management_user_ids(
                        tenant_id=work_order.tenant_id,
                        property_id=work_order.property_id,
                    )
                )
                assignee_user_id = self._repository.get_active_assignee_user_id(
                    work_order.id
                )
                if assignee_user_id is not None:
                    recipients.add(assignee_user_id)
                for user_id in sorted(recipients):
                    notifier.enqueue(
                        tenant_id=work_order.tenant_id,
                        property_id=work_order.property_id,
                        user_id=user_id,
                        title=(
                            "工单 SLA 即将超时"
                            if target is SlaClockState.WARNING
                            else "工单 SLA 已超时"
                        ),
                        body=(
                            "Work order is approaching its completion deadline"
                            if target is SlaClockState.WARNING
                            else "Work order is overdue and requires escalation"
                        ),
                        resource_type="WORK_ORDER",
                        resource_id=work_order.id,
                    )
                    counts["notifications"] += 1
        return SlaSweepResult(**counts)

    def sweep_assignment_deadlines(
        self,
        *,
        now: datetime | None = None,
        limit: int = 100,
        work_order_id: int | None = None,
    ) -> AssignmentSlaSweepResult:
        """Expire unaccepted assignments and escalate accepted-but-idle work."""

        effective_now = now or utc_now()
        bounded_limit = min(max(limit, 1), 500)
        counts = {
            "scanned": 0,
            "accept_timeouts": 0,
            "response_timeouts": 0,
            "notifications": 0,
            "redispatch_enqueued": 0,
        }
        redispatch: list[tuple[int, str]] = []
        with begin_owned_transaction(self._session):
            notifier = NotificationService(self._session, settings=self._settings)
            records = self._repository.list_due_assignment_sla_work_orders_for_update(
                now=effective_now,
                limit=bounded_limit,
                work_order_id=work_order_id,
            )
            for record in records:
                counts["scanned"] += 1
                clock = self._repository.get_sla_clock_for_update(record.id)
                if clock is None:
                    continue
                if record.status == WorkOrderStatus.ASSIGNED.value:
                    result = self._expire_unaccepted_assignment(
                        record=record,
                        clock=clock,
                        now=effective_now,
                        notifier=notifier,
                    )
                    if result is None:
                        continue
                    notification_count, request_id = result
                    counts["accept_timeouts"] += 1
                    counts["notifications"] += notification_count
                    redispatch.append((record.id, request_id))
                    continue
                if record.status == WorkOrderStatus.ACCEPTED.value:
                    notification_count = self._escalate_response_timeout(
                        record=record,
                        clock=clock,
                        now=effective_now,
                        notifier=notifier,
                    )
                    if notification_count is None:
                        continue
                    counts["response_timeouts"] += 1
                    counts["notifications"] += notification_count

        for target_id, request_id in redispatch:
            if self._enqueue_redispatch(target_id, request_id):
                counts["redispatch_enqueued"] += 1
        return AssignmentSlaSweepResult(**counts)

    def sweep_unavailable_assignments(self) -> int:
        """Only unaccepted unavailable reservations; never close processing work."""
        from app.repository.ai import AiRepository
        ai = AiRepository(self._session)
        now = utc_now()
        with begin_owned_transaction(self._session):
            ids = self._repository.list_unavailable_unaccepted(now=now)
        changed = 0
        for work_order_id in ids:
            with begin_owned_transaction(self._session):
                record = self._repository.get_work_order_for_update(work_order_id)
                if record is None or record.status != "ASSIGNED":
                    continue
                assignment = self._repository.get_active_assignment_for_update(record.id)
                if assignment is None:
                    continue
                employee = self._employees.get_employee_for_update(assignment.employee_id)
                property_record = self._repository.get_property(record.property_id)
                if property_record is None:
                    continue
                day = current_business_date(property_record.timezone, clock=lambda: now)
                daily = self._employees.get_daily_status_for_update(employee_id=assignment.employee_id, property_id=record.property_id, business_date=day)
                effective = ai.get_effective_property(employee_id=assignment.employee_id, property_id=record.property_id, now=now)
                attendance = ai.get_attendance(employee_id=assignment.employee_id, property_id=record.property_id, business_date=day)
                available = (employee is not None and employee.status == "ACTIVE"
                             and ai.get_user_status(employee.user_id) == "ACTIVE" and effective is not None
                             and daily is not None and daily.on_duty == "ON_DUTY" and attendance is not None)
                if available:
                    continue
                outcome = self._expire_unaccepted_assignment(record=record,
                    clock=self._repository.get_sla_clock_for_update(record.id), now=now,
                    notifier=NotificationService(self._session, settings=self._settings),
                    reason_code="ASSIGNEE_UNAVAILABLE")
                changed += int(outcome is not None)
        return changed

    def _expire_unaccepted_assignment(
        self,
        *,
        record,
        clock,
        now: datetime,
        notifier: NotificationService,
        reason_code: str = "SLA_ACCEPT_TIMEOUT",
    ) -> tuple[int, str] | None:
        assignment = self._repository.get_active_assignment_for_update(record.id)
        if (
            assignment is None
            or record.current_assignment_id != assignment.id
            or (reason_code == "SLA_ACCEPT_TIMEOUT" and (clock is None or now < clock.accept_due_at))
        ):
            return None
        suffix = "accept-timeout" if reason_code == "SLA_ACCEPT_TIMEOUT" else reason_code.lower()
        request_id = f"sla-assignment:{assignment.id}:{suffix}"
        assignee_user_id = self._repository.get_active_assignee_user_id(record.id)
        employee = self._employees.get_employee(assignment.employee_id)
        assignment.status = AssignmentStatus.REASSIGNED.value
        if reason_code == "SLA_ACCEPT_TIMEOUT":
            assignment.timeout_at = now
        assignment.ended_at = now
        assignment.reason = reason_code
        from app.service.incident import IncidentCapacityService

        release_load = IncidentCapacityService(
            self._session,
            work_order_repository=self._repository,
        ).should_release_load(
            work_order=record,
            assignment=assignment,
        )
        if employee is not None and release_load:
            property_record = self._repository.get_property(record.property_id)
            if property_record is not None:
                business_date = current_business_date(
                    property_record.timezone,
                    clock=lambda: now,
                )
                daily = self._employees.get_daily_status_for_update(
                    employee_id=employee.id,
                    property_id=record.property_id,
                    business_date=business_date,
                )
                if daily is not None:
                    daily.current_load = max(daily.current_load - 1, 0)

        from_status = WorkOrderStatus(record.status)
        require_legal_transition(
            from_status=from_status,
            to_status=WorkOrderStatus.PENDING_ASSIGNMENT,
            event=WorkOrderEvent.REASSIGN,
        )
        record.status = WorkOrderStatus.PENDING_ASSIGNMENT.value
        record.version += 1
        record.current_assignment_id = None
        self._repository.add_log(
            WorkOrderLog(
                tenant_id=record.tenant_id,
                property_id=record.property_id,
                work_order_id=record.id,
                actor_user_id=None,
                actor_type=WorkOrderActorType.SYSTEM.value,
                from_status=from_status.value,
                to_status=record.status,
                process_reason=record.process_reason,
                event_code=WorkOrderEvent.REASSIGN.value,
                request_id=request_id,
                payload_json={
                    "event_code": WorkOrderEvent.REASSIGN.value,
                    "reason_code": reason_code,
                    "assignment_id": assignment.id,
                    "employee_id": assignment.employee_id,
                    "accept_due_at": None if clock is None else clock.accept_due_at.isoformat(),
                },
            )
        )
        self._audit.append(
            action=AuditAction.WORK_ORDER_REASSIGNED,
            resource_type=AuditResourceType.WORK_ORDER_ASSIGNMENT,
            actor_user_id=None,
            resource_id=assignment.id,
            request_id=request_id,
            scope_type=ScopeType.PROPERTY,
            tenant_id=record.tenant_id,
            property_id=record.property_id,
            details={
                "work_order_id": record.id,
                "employee_id": assignment.employee_id,
                "status": record.status,
                "reason_code": reason_code,
            },
        )
        recipients = set(
            self._repository.list_sla_management_user_ids(
                tenant_id=record.tenant_id,
                property_id=record.property_id,
            )
        )
        if assignee_user_id is not None:
            recipients.add(assignee_user_id)
        for user_id in sorted(recipients):
            notifier.enqueue(
                tenant_id=record.tenant_id,
                property_id=record.property_id,
                user_id=user_id,
                title="工单等待重新派单",
                body="原派单已失效，系统将继续安排处理人员",
                resource_type="WORK_ORDER",
                resource_id=record.id,
            )
        return len(recipients), request_id

    def _escalate_response_timeout(
        self,
        *,
        record,
        clock,
        now: datetime,
        notifier: NotificationService,
    ) -> int | None:
        assignment = self._repository.get_active_assignment_for_update(record.id)
        if (
            assignment is None
            or record.current_assignment_id != assignment.id
            or assignment.accept_at is None
        ):
            return None
        request_id = f"sla-assignment:{assignment.id}:response-timeout"
        if self._repository.has_log_request_id(
            work_order_id=record.id,
            request_id=request_id,
        ):
            return None
        assignment.timeout_at = now
        self._repository.add_log(
            WorkOrderLog(
                tenant_id=record.tenant_id,
                property_id=record.property_id,
                work_order_id=record.id,
                actor_user_id=None,
                actor_type=WorkOrderActorType.SYSTEM.value,
                from_status=record.status,
                to_status=record.status,
                process_reason=record.process_reason,
                event_code=WorkOrderEvent.SLA_RESPONSE_TIMEOUT.value,
                request_id=request_id,
                payload_json={
                    "event_code": WorkOrderEvent.SLA_RESPONSE_TIMEOUT.value,
                    "assignment_id": assignment.id,
                    "employee_id": assignment.employee_id,
                    "respond_due_at": clock.respond_due_at.isoformat(),
                    "action": "SUPERVISOR_REVIEW",
                },
            )
        )
        recipients = set(
            self._repository.list_sla_management_user_ids(
                tenant_id=record.tenant_id,
                property_id=record.property_id,
            )
        )
        assignee_user_id = self._repository.get_active_assignee_user_id(record.id)
        if assignee_user_id is not None:
            recipients.add(assignee_user_id)
        for user_id in sorted(recipients):
            notifier.enqueue(
                tenant_id=record.tenant_id,
                property_id=record.property_id,
                user_id=user_id,
                title="Work order response timeout",
                body="The accepted work order has not started and needs supervisor review",
                resource_type="WORK_ORDER",
                resource_id=record.id,
            )
        return len(recipients)

    def _enqueue_redispatch(self, work_order_id: int, request_id: str) -> bool:
        if not self._settings.should_enqueue_ai_on_create:
            return False
        from app.worker.enqueue import enqueue_celery_task
        from app.worker.tasks import analyze_work_order_task

        return enqueue_celery_task(
            settings=self._settings,
            celery_task=analyze_work_order_task,
            args=(work_order_id, request_id),
            event="ai_enqueue_failed",
            extra={"work_order_id": work_order_id},
        )

    @staticmethod
    def _target_state(*, clock, now: datetime) -> SlaClockState | None:
        if now >= clock.complete_due_at:
            return SlaClockState.OVERDUE
        if clock.state == SlaClockState.OPEN.value and now >= clock.warning_at:
            return SlaClockState.WARNING
        return None
