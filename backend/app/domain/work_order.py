"""Pure WorkOrder FSM, process-reason, SLA, and eligibility policy."""

from datetime import datetime, timedelta
from enum import StrEnum


class WorkOrderStatus(StrEnum):
    PENDING_ANALYSIS = "PENDING_ANALYSIS"
    PENDING_ASSIGNMENT = "PENDING_ASSIGNMENT"
    ASSIGNED = "ASSIGNED"
    ACCEPTED = "ACCEPTED"
    PROCESSING = "PROCESSING"
    WAIT_CONFIRM = "WAIT_CONFIRM"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    CLOSED = "CLOSED"


class ProcessReason(StrEnum):
    NONE = "NONE"
    WAITING_OWNER = "WAITING_OWNER"
    WAITING_PARTS = "WAITING_PARTS"
    INTERNAL_UNRESOLVED = "INTERNAL_UNRESOLVED"
    ESCALATED = "ESCALATED"
    EXTERNAL_COORDINATION = "EXTERNAL_COORDINATION"
    REASSIGN_REQUIRED = "REASSIGN_REQUIRED"


class WorkOrderEvent(StrEnum):
    CREATE = "CREATE"
    ANALYZE = "ANALYZE"
    ASSIGN = "ASSIGN"
    REASSIGN = "REASSIGN"
    ACCEPT = "ACCEPT"
    REJECT = "REJECT"
    START = "START"
    PROCESS_UPDATE = "PROCESS_UPDATE"
    RECEIPT_RESOLVED = "RECEIPT_RESOLVED"
    RECEIPT_UNRESOLVED = "RECEIPT_UNRESOLVED"
    ESCALATE = "ESCALATE"
    DECIDE = "DECIDE"
    CONFIRM = "CONFIRM"
    REOPEN = "REOPEN"
    CANCEL = "CANCEL"
    ARCHIVE = "ARCHIVE"
    RATE = "RATE"
    APPEND = "APPEND"


class WorkOrderCategory(StrEnum):
    PLUMBING = "PLUMBING"
    ELECTRICAL = "ELECTRICAL"
    HVAC = "HVAC"
    CLEANING = "CLEANING"
    SECURITY = "SECURITY"
    OTHER = "OTHER"


class WorkOrderPriority(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    URGENT = "URGENT"


class WorkOrderSeverity(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class AssignmentStatus(StrEnum):
    ACTIVE = "ACTIVE"
    ENDED = "ENDED"
    REJECTED = "REJECTED"
    REASSIGNED = "REASSIGNED"


class MediaKind(StrEnum):
    IMAGE = "IMAGE"
    AUDIO = "AUDIO"


class MediaTakenPhase(StrEnum):
    REPORT = "REPORT"
    BEFORE = "BEFORE"
    AFTER = "AFTER"
    ON_SITE = "ON_SITE"


class SlaClockState(StrEnum):
    OPEN = "OPEN"
    WARNING = "WARNING"
    OVERDUE = "OVERDUE"
    STOPPED = "STOPPED"


class WorkOrderActorType(StrEnum):
    RESIDENT = "RESIDENT"
    STAFF = "STAFF"
    SUPERVISOR = "SUPERVISOR"
    ADMIN = "ADMIN"
    SYSTEM = "SYSTEM"


class SupervisorDecision(StrEnum):
    REASSIGN = "REASSIGN"
    WAIT_PARTS = "WAIT_PARTS"
    WAIT_OWNER = "WAIT_OWNER"
    EXTERNAL_COORDINATION = "EXTERNAL_COORDINATION"
    CONTINUE = "CONTINUE"


WORK_ORDER_CREATE = "work_order:create"
WORK_ORDER_READ = "work_order:read"
WORK_ORDER_DISPATCH = "work_order:dispatch"
WORK_ORDER_PROCESS = "work_order:process"
WORK_ORDER_RESOLVE = "work_order:resolve"
WORK_ORDER_DECIDE = "work_order:decide"
WORK_ORDER_CANCEL = "work_order:cancel"

WAITING_PROCESS_REASONS = frozenset(
    {
        ProcessReason.WAITING_OWNER,
        ProcessReason.WAITING_PARTS,
        ProcessReason.INTERNAL_UNRESOLVED,
        ProcessReason.ESCALATED,
        ProcessReason.EXTERNAL_COORDINATION,
        ProcessReason.REASSIGN_REQUIRED,
    }
)

STAFF_MAY_COMPLETE_REASONS = frozenset({ProcessReason.NONE})

TERMINAL_STATUSES = frozenset(
    {
        WorkOrderStatus.COMPLETED,
        WorkOrderStatus.CANCELLED,
        WorkOrderStatus.CLOSED,
    }
)

PENDING_DISPATCH_STATUSES = frozenset(
    {
        WorkOrderStatus.PENDING_ANALYSIS,
        WorkOrderStatus.PENDING_ASSIGNMENT,
    }
)

ACTIVE_HANDLING_STATUSES = frozenset(
    {
        WorkOrderStatus.ASSIGNED,
        WorkOrderStatus.ACCEPTED,
        WorkOrderStatus.PROCESSING,
    }
)

# Default complete-window minutes by priority. Respond is complete / 8 (min 15).
DEFAULT_SLA_COMPLETE_MINUTES: dict[WorkOrderPriority, int] = {
    WorkOrderPriority.URGENT: 120,
    WorkOrderPriority.HIGH: 480,
    WorkOrderPriority.MEDIUM: 1440,
    WorkOrderPriority.LOW: 4320,
}

DEFAULT_SLA_RESPOND_MINUTES: dict[WorkOrderPriority, int] = {
    WorkOrderPriority.URGENT: 15,
    WorkOrderPriority.HIGH: 30,
    WorkOrderPriority.MEDIUM: 120,
    WorkOrderPriority.LOW: 480,
}

_LEGAL_TRANSITIONS: frozenset[tuple[WorkOrderStatus, WorkOrderStatus, WorkOrderEvent]] = (
    frozenset(
        {
            (
                WorkOrderStatus.PENDING_ANALYSIS,
                WorkOrderStatus.PENDING_ASSIGNMENT,
                WorkOrderEvent.ANALYZE,
            ),
            (
                WorkOrderStatus.PENDING_ANALYSIS,
                WorkOrderStatus.PENDING_ASSIGNMENT,
                WorkOrderEvent.ASSIGN,
            ),
            (
                WorkOrderStatus.PENDING_ANALYSIS,
                WorkOrderStatus.CANCELLED,
                WorkOrderEvent.CANCEL,
            ),
            (
                WorkOrderStatus.PENDING_ASSIGNMENT,
                WorkOrderStatus.ASSIGNED,
                WorkOrderEvent.ASSIGN,
            ),
            (
                WorkOrderStatus.PENDING_ASSIGNMENT,
                WorkOrderStatus.CANCELLED,
                WorkOrderEvent.CANCEL,
            ),
            (
                WorkOrderStatus.ASSIGNED,
                WorkOrderStatus.ACCEPTED,
                WorkOrderEvent.ACCEPT,
            ),
            (
                WorkOrderStatus.ASSIGNED,
                WorkOrderStatus.PENDING_ASSIGNMENT,
                WorkOrderEvent.REJECT,
            ),
            (
                WorkOrderStatus.ASSIGNED,
                WorkOrderStatus.PENDING_ASSIGNMENT,
                WorkOrderEvent.REASSIGN,
            ),
            (
                WorkOrderStatus.ASSIGNED,
                WorkOrderStatus.CANCELLED,
                WorkOrderEvent.CANCEL,
            ),
            (
                WorkOrderStatus.ACCEPTED,
                WorkOrderStatus.PROCESSING,
                WorkOrderEvent.START,
            ),
            (
                WorkOrderStatus.ACCEPTED,
                WorkOrderStatus.PENDING_ASSIGNMENT,
                WorkOrderEvent.REASSIGN,
            ),
            (
                WorkOrderStatus.ACCEPTED,
                WorkOrderStatus.CANCELLED,
                WorkOrderEvent.CANCEL,
            ),
            (
                WorkOrderStatus.PROCESSING,
                WorkOrderStatus.PROCESSING,
                WorkOrderEvent.PROCESS_UPDATE,
            ),
            (
                WorkOrderStatus.PROCESSING,
                WorkOrderStatus.PROCESSING,
                WorkOrderEvent.RECEIPT_UNRESOLVED,
            ),
            (
                WorkOrderStatus.PROCESSING,
                WorkOrderStatus.PROCESSING,
                WorkOrderEvent.ESCALATE,
            ),
            (
                WorkOrderStatus.PROCESSING,
                WorkOrderStatus.PROCESSING,
                WorkOrderEvent.DECIDE,
            ),
            (
                WorkOrderStatus.PROCESSING,
                WorkOrderStatus.WAIT_CONFIRM,
                WorkOrderEvent.RECEIPT_RESOLVED,
            ),
            (
                WorkOrderStatus.PROCESSING,
                WorkOrderStatus.PENDING_ASSIGNMENT,
                WorkOrderEvent.REASSIGN,
            ),
            (
                WorkOrderStatus.WAIT_CONFIRM,
                WorkOrderStatus.COMPLETED,
                WorkOrderEvent.CONFIRM,
            ),
            (
                WorkOrderStatus.WAIT_CONFIRM,
                WorkOrderStatus.PROCESSING,
                WorkOrderEvent.REOPEN,
            ),
            (
                WorkOrderStatus.WAIT_CONFIRM,
                WorkOrderStatus.PENDING_ASSIGNMENT,
                WorkOrderEvent.REOPEN,
            ),
            (
                WorkOrderStatus.COMPLETED,
                WorkOrderStatus.CLOSED,
                WorkOrderEvent.ARCHIVE,
            ),
            (
                WorkOrderStatus.CANCELLED,
                WorkOrderStatus.CLOSED,
                WorkOrderEvent.ARCHIVE,
            ),
        }
    )
)


def is_legal_transition(
    *,
    from_status: WorkOrderStatus | str,
    to_status: WorkOrderStatus | str,
    event: WorkOrderEvent | str,
) -> bool:
    current = WorkOrderStatus(from_status)
    target = WorkOrderStatus(to_status)
    code = WorkOrderEvent(event)
    return (current, target, code) in _LEGAL_TRANSITIONS


def require_legal_transition(
    *,
    from_status: WorkOrderStatus | str,
    to_status: WorkOrderStatus | str,
    event: WorkOrderEvent | str,
) -> tuple[WorkOrderStatus, WorkOrderStatus, WorkOrderEvent]:
    current = WorkOrderStatus(from_status)
    target = WorkOrderStatus(to_status)
    code = WorkOrderEvent(event)
    if (current, target, code) not in _LEGAL_TRANSITIONS:
        raise ValueError(
            f"Illegal work-order transition {current.value} -> {target.value} "
            f"via {code.value}"
        )
    return current, target, code


def process_reason_for_status(
    *,
    status: WorkOrderStatus | str,
    process_reason: ProcessReason | str,
) -> ProcessReason:
    reason = ProcessReason(process_reason)
    current = WorkOrderStatus(status)
    if current is not WorkOrderStatus.PROCESSING:
        return ProcessReason.NONE
    return reason


def staff_may_submit_resolved_receipt(process_reason: ProcessReason | str) -> bool:
    return ProcessReason(process_reason) in STAFF_MAY_COMPLETE_REASONS


def sla_windows_for_priority(
    priority: WorkOrderPriority | str,
) -> tuple[int, int]:
    code = WorkOrderPriority(priority)
    return (
        DEFAULT_SLA_RESPOND_MINUTES[code],
        DEFAULT_SLA_COMPLETE_MINUTES[code],
    )


def compute_sla_due_at(
    *,
    created_at: datetime,
    priority: WorkOrderPriority | str,
    complete_minutes: int | None = None,
) -> datetime:
    minutes = complete_minutes
    if minutes is None:
        minutes = DEFAULT_SLA_COMPLETE_MINUTES[WorkOrderPriority(priority)]
    return created_at + timedelta(minutes=minutes)


def sla_is_overdue(*, due_at: datetime | None, now: datetime) -> bool:
    if due_at is None:
        return False
    return now > due_at


def employee_is_capacity_available(*, current_load: int, max_capacity: int) -> bool:
    return current_load < max_capacity


LEGAL_TRANSITION_MATRIX: tuple[
    tuple[WorkOrderStatus, WorkOrderStatus, WorkOrderEvent], ...
] = tuple(sorted(_LEGAL_TRANSITIONS, key=lambda item: (item[0], item[2], item[1])))
