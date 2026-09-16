"""Pure WorkOrder FSM and SLA policy tests."""

import pytest

from app.domain.work_order import (
    ProcessReason,
    WorkOrderEvent,
    WorkOrderPriority,
    WorkOrderStatus,
    compute_sla_due_at,
    is_legal_transition,
    require_legal_transition,
    sla_is_overdue,
    sla_windows_for_priority,
    staff_may_submit_resolved_receipt,
)
from datetime import UTC, datetime, timedelta


def test_legal_happy_path_transitions() -> None:
    path = (
        (WorkOrderStatus.PENDING_ANALYSIS, WorkOrderStatus.PENDING_ASSIGNMENT, WorkOrderEvent.ANALYZE),
        (WorkOrderStatus.PENDING_ASSIGNMENT, WorkOrderStatus.ASSIGNED, WorkOrderEvent.ASSIGN),
        (WorkOrderStatus.ASSIGNED, WorkOrderStatus.ACCEPTED, WorkOrderEvent.ACCEPT),
        (WorkOrderStatus.ACCEPTED, WorkOrderStatus.PROCESSING, WorkOrderEvent.START),
        (WorkOrderStatus.PROCESSING, WorkOrderStatus.WAIT_CONFIRM, WorkOrderEvent.RECEIPT_RESOLVED),
        (WorkOrderStatus.WAIT_CONFIRM, WorkOrderStatus.COMPLETED, WorkOrderEvent.CONFIRM),
        (WorkOrderStatus.COMPLETED, WorkOrderStatus.CLOSED, WorkOrderEvent.ARCHIVE),
    )
    for current, target, event in path:
        assert is_legal_transition(from_status=current, to_status=target, event=event)


def test_manual_assign_from_pending_analysis_is_legal() -> None:
    assert is_legal_transition(
        from_status=WorkOrderStatus.PENDING_ANALYSIS,
        to_status=WorkOrderStatus.PENDING_ASSIGNMENT,
        event=WorkOrderEvent.ASSIGN,
    )


def test_illegal_closed_to_in_progress() -> None:
    assert not is_legal_transition(
        from_status=WorkOrderStatus.CLOSED,
        to_status=WorkOrderStatus.PROCESSING,
        event=WorkOrderEvent.START,
    )
    with pytest.raises(ValueError):
        require_legal_transition(
            from_status=WorkOrderStatus.ASSIGNED,
            to_status=WorkOrderStatus.CLOSED,
            event=WorkOrderEvent.ARCHIVE,
        )


def test_reopen_is_event_not_status() -> None:
    assert "REOPEN" not in {status.value for status in WorkOrderStatus}
    assert is_legal_transition(
        from_status=WorkOrderStatus.WAIT_CONFIRM,
        to_status=WorkOrderStatus.PROCESSING,
        event=WorkOrderEvent.REOPEN,
    )


def test_staff_cannot_complete_waiting_reasons() -> None:
    assert staff_may_submit_resolved_receipt(ProcessReason.NONE)
    for reason in (
        ProcessReason.WAITING_OWNER,
        ProcessReason.WAITING_PARTS,
        ProcessReason.INTERNAL_UNRESOLVED,
        ProcessReason.ESCALATED,
        ProcessReason.EXTERNAL_COORDINATION,
        ProcessReason.REASSIGN_REQUIRED,
    ):
        assert not staff_may_submit_resolved_receipt(reason)


def test_sla_priority_windows_and_overdue() -> None:
    urgent_respond, urgent_complete = sla_windows_for_priority(WorkOrderPriority.URGENT)
    low_respond, low_complete = sla_windows_for_priority(WorkOrderPriority.LOW)
    assert urgent_complete < low_complete
    assert urgent_respond < low_respond
    created = datetime(2026, 9, 17, 0, 0, tzinfo=UTC)
    due = compute_sla_due_at(created_at=created, priority=WorkOrderPriority.MEDIUM)
    assert due == created + timedelta(minutes=1440)
    assert not sla_is_overdue(due_at=due, now=due)
    assert sla_is_overdue(due_at=due, now=due + timedelta(seconds=1))
