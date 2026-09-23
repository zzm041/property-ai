"""Current-schema reopen/history behavior; no database fixtures or network."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import Request

from app.api.v1.work_orders import get_work_order
from app.core.errors import AppError, IamErrorCode, WorkOrderErrorCode
from app.domain.work_order import WorkOrderEvent
from app.service.work_order import WorkOrderService


NOW = datetime(2026, 9, 23, 2, tzinfo=UTC)


def record(status="WAIT_CONFIRM"):
    return SimpleNamespace(
        id=74, tenant_id=8, property_id=9, house_id=27, reporter_user_id=31,
        status=status, process_reason="NONE", category="PLUMBING",
        severity="LOW", priority="MEDIUM", version=6,
        current_assignment_id=12, incident_id=None, sla_due_at=NOW,
        assigned_at=NOW, accepted_at=NOW, started_at=NOW,
        resolved_at=NOW, closed_at=None, created_at=NOW, updated_at=NOW,
    )


def service_for_reopen(*, eligible=True, status="WAIT_CONFIRM"):
    service = WorkOrderService.__new__(WorkOrderService)
    current = MagicMock(user_id=31)
    work_order = record(status)
    process = SimpleNamespace(id=13, cycle_no=2, assignee_id=45, ended_at=NOW)
    service._reload_actor = MagicMock(return_value=current)
    service._repository = MagicMock()
    service._repository.get_work_order_for_update.return_value = work_order
    service._repository.get_active_assignment_for_update.return_value = SimpleNamespace(
        id=12, employee_id=45,
    )
    service._repository.get_current_process.return_value = process
    service._repository.get_sla_clock_for_update.return_value = None
    service._employees = MagicMock()
    service._employees.get_employee.return_value = SimpleNamespace(id=45, user_id=46)
    service._eligibility_error = MagicMock(return_value=None if eligible else AppError(
        code=WorkOrderErrorCode.EMPLOYEE_NOT_ELIGIBLE, message="off duty", status_code=409,
    ))
    service._end_active_assignment = MagicMock()
    service._append_audit = MagicMock()
    service._notify_users = MagicMock()
    service._session = MagicMock()
    service._clock = lambda: NOW + timedelta(minutes=1)
    return service, current, work_order, process


def reopen(service, actor, *, version=6):
    return service._reopen(
        actor=actor, work_order_id=74, request_id="wo014-unit",
        reason="Still leaking after repair", version=version,
    )


@pytest.mark.parametrize("reason", [None, "", " \n\t", "a" * 256])
def test_reason_rejected_before_transaction(reason):
    service = WorkOrderService.__new__(WorkOrderService)
    service._execute = MagicMock()
    with pytest.raises(AppError) as error:
        service.reopen(actor=MagicMock(), work_order_id=74, request_id="wo014", reason=reason)
    assert error.value.code == WorkOrderErrorCode.INVALID_INPUT
    service._execute.assert_not_called()


@pytest.mark.parametrize("state, expected_enqueues", [("PROCESSING", 0), ("PENDING_ASSIGNMENT", 1)])
def test_reopen_enqueues_only_after_commit_for_pending_assignment(state, expected_enqueues):
    service = WorkOrderService.__new__(WorkOrderService)
    order = record(state)
    events = []
    service._reopen = MagicMock(return_value=order)

    def commit(operation):
        result = operation()
        events.append("committed")
        return result

    def enqueue(*args):
        assert events == ["committed"]
        events.append("enqueued")

    service._execute = commit
    service._enqueue_analysis = MagicMock(side_effect=enqueue)
    assert service.reopen(
        actor=MagicMock(), work_order_id=74, request_id="wo014", reason="  Still leaking  ",
        version=6,
    ) is order
    assert service._reopen.call_args.kwargs["reason"] == "Still leaking"
    assert service._enqueue_analysis.call_count == expected_enqueues


def test_reopen_failed_transaction_never_enqueues():
    service = WorkOrderService.__new__(WorkOrderService)
    service._execute = MagicMock(side_effect=AppError(
        code=WorkOrderErrorCode.CONFLICT, message="conflict", status_code=409,
    ))
    service._enqueue_analysis = MagicMock()
    with pytest.raises(AppError):
        service.reopen(actor=MagicMock(), work_order_id=74, request_id="wo014", reason="leak")
    service._enqueue_analysis.assert_not_called()


def test_original_assignee_reopen_preserves_previous_process_and_adds_one_cycle():
    service, actor, order, previous = service_for_reopen()
    previous_values = vars(previous).copy()
    assert reopen(service, actor) is order
    assert order.status == "PROCESSING"
    assert order.process_reason == "NONE"
    assert order.version == 7 and order.resolved_at is None
    assert vars(previous) == previous_values
    new_cycle = service._repository.add_process.call_args.args[0]
    assert (new_cycle.cycle_no, new_cycle.assignee_id) == (3, 45)
    feedback = service._repository.add_feedback.call_args.args[0]
    assert (feedback.process_id, feedback.unresolved_flag, feedback.confirmed) == (13, 1, 0)
    assert feedback.comment == "Still leaking after repair"
    log = service._repository.add_log.call_args.args[0]
    assert log.event_code == "REOPEN"
    assert log.payload_json["reason"] == feedback.comment
    service._repository.add_receipt.assert_not_called()
    service._end_active_assignment.assert_not_called()
    service._eligibility_error.assert_called_once_with(
        order, service._employees.get_employee.return_value, ignore_capacity=True,
    )


def test_ineligible_original_assignee_ends_assignment_without_fake_process():
    service, actor, order, previous = service_for_reopen(eligible=False)
    reopen(service, actor)
    assert order.status == "PENDING_ASSIGNMENT" and order.resolved_at is None
    assert previous.cycle_no == 2
    service._repository.add_process.assert_not_called()
    service._repository.add_receipt.assert_not_called()
    service._end_active_assignment.assert_called_once()
    service._repository.add_feedback.assert_called_once()


def test_other_resident_cannot_reopen():
    service, actor, _, _ = service_for_reopen()
    actor.user_id = 99
    with pytest.raises(AppError) as error:
        reopen(service, actor)
    assert error.value.code == IamErrorCode.FORBIDDEN
    service._repository.add_feedback.assert_not_called()
    service._repository.add_process.assert_not_called()


def test_reporter_without_property_grant_cannot_reopen():
    service, actor, _, _ = service_for_reopen()
    actor.has_permission.return_value = False
    with pytest.raises(AppError) as error:
        reopen(service, actor)
    assert error.value.code == IamErrorCode.FORBIDDEN
    service._repository.add_feedback.assert_not_called()


def test_stale_version_blocks_before_mutation():
    service, actor, _, _ = service_for_reopen()
    with pytest.raises(AppError) as error:
        reopen(service, actor, version=5)
    assert error.value.code == WorkOrderErrorCode.CONFLICT
    service._repository.add_feedback.assert_not_called()
    service._repository.add_process.assert_not_called()


@pytest.mark.parametrize("state", ["PROCESSING", "CLOSED", "CANCELLED", "ASSIGNED"])
def test_illegal_state_uses_existing_fsm_and_never_adds_cycle(state):
    service, actor, _, _ = service_for_reopen(status=state)
    with pytest.raises(AppError) as error:
        reopen(service, actor)
    assert error.value.code == WorkOrderErrorCode.INVALID_TRANSITION
    service._repository.add_process.assert_not_called()
    service._append_audit.assert_not_called()
    # Actual service-owned transaction rolls back the attempted feedback. This
    # mock-only unit does not claim real DB transaction/concurrency coverage.


def test_duplicate_same_version_does_not_add_another_cycle_or_feedback():
    service, actor, _, _ = service_for_reopen()
    reopen(service, actor)
    with pytest.raises(AppError) as error:
        reopen(service, actor)
    assert error.value.code == WorkOrderErrorCode.CONFLICT
    service._repository.add_process.assert_called_once()
    service._repository.add_feedback.assert_called_once()


@pytest.mark.parametrize("events, expected", [
    ([], "OPEN"), (["SLA_WARNING"], "WARNING"),
    (["SLA_WARNING", "SLA_OVERDUE"], "OVERDUE"),
])
def test_reopen_sla_preserves_deadline_and_previous_alert_progress(events, expected):
    service, actor, order, _ = service_for_reopen()
    clock = SimpleNamespace(state="STOPPED", warning_at=NOW, complete_due_at=NOW)
    service._repository.get_sla_clock_for_update.return_value = clock
    service._repository.list_logs.return_value = [SimpleNamespace(event_code=e) for e in events]
    reopen(service, actor)
    assert clock.state == expected
    assert clock.warning_at == clock.complete_due_at == order.sla_due_at == NOW
    assert service._repository.add_log.call_args.args[0].event_code == WorkOrderEvent.REOPEN.value


def test_detail_serializes_all_receipts_and_feedback_by_actual_process_id():
    service = MagicMock()
    service.is_overdue.return_value = False
    processes = tuple(SimpleNamespace(
        id=cycle + 10, cycle_no=cycle, assignee_id=45 + cycle,
        started_at=NOW, ended_at=NOW,
    ) for cycle in (1, 2))
    receipts = tuple(SimpleNamespace(
        id=cycle, process_id=cycle + 10, problem_type="PLUMBING",
        method=f"cycle-{cycle}-repair", materials="seal", fee_amount=None, fee_flag="NONE",
        resolved=1, unresolved_reason=None, follow_up="check again", finished_at=NOW,
    ) for cycle in (1, 2))
    feedback = SimpleNamespace(
        id=8, process_id=11, actor_user_id=31, confirmed=0, rating=None,
        comment="still leaking", unresolved_flag=1, created_at=NOW,
    )
    media = SimpleNamespace(
        id=12, file_id=70, kind="IMAGE", taken_phase="AFTER",
        uploader_user_id=46, created_at=NOW,
    )
    service.get_work_order_detail.return_value = (
        record(), None, (media,), (), processes, receipts, (feedback,), (),
    )
    request = Request({"type": "http", "headers": []})
    request.state.request_id = "wo014-detail"
    data = get_work_order(74, request, MagicMock(), service)["data"]
    assert [p["cycle_no"] for p in data["processes"]] == [1, 2]
    assert [r["method"] for r in data["receipts"]] == ["cycle-1-repair", "cycle-2-repair"]
    assert [(r["cycle_no"], r["assignee_id"]) for r in data["receipts"]] == [(1, 46), (2, 47)]
    assert data["feedbacks"][0]["process_id"] == 11
    assert data["media"][0]["created_at"] == "2026-09-23T02:00:00Z"
    assert "process_id" not in data["media"][0]  # Never invent a media/cycle relationship.
