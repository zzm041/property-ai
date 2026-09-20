"""Idempotent SLA warning and overdue escalation scheduling."""

from dataclasses import asdict, dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.database import begin_owned_transaction
from app.core.time import utc_now
from app.domain.work_order import SlaClockState, TERMINAL_STATUSES, WorkOrderEvent
from app.model.work_order import WorkOrderLog
from app.repository.work_order import WorkOrderRepository
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

    @staticmethod
    def _target_state(*, clock, now: datetime) -> SlaClockState | None:
        if now >= clock.complete_due_at:
            return SlaClockState.OVERDUE
        if clock.state == SlaClockState.OPEN.value and now >= clock.warning_at:
            return SlaClockState.WARNING
        return None
