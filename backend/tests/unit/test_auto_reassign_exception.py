"""Automatic redispatch and exception-decision regression coverage."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.core.config import Settings
from app.core.errors import AppError, WorkOrderErrorCode
from app.domain.ai import AnalysisStatus
from app.domain.work_order import AttendanceOnDuty, EmployeeStatus
from app.domain.work_order import AssignmentStatus, ProcessReason
from app.model.work_order import WorkOrder
from app.service.analysis import AnalysisService
from app.service.auto_dispatch import AutoDispatchService
from app.service.work_order import WorkOrderService


REPO_ROOT = Path(__file__).resolve().parents[3]


def _pending_order() -> WorkOrder:
    record = WorkOrder()
    record.id = 41
    record.tenant_id = 1
    record.property_id = 2
    record.house_id = 3
    record.incident_id = None
    record.status = "PENDING_ASSIGNMENT"
    record.process_reason = "NONE"
    record.current_assignment_id = None
    record.version = 4
    return record


def test_reject_enqueues_redispatch_only_after_business_transaction() -> None:
    service = WorkOrderService.__new__(WorkOrderService)
    record = _pending_order()
    record.status = "PENDING_ASSIGNMENT"
    service._validate_request_id = MagicMock()
    service._reject = MagicMock(return_value=record)
    service._execute = MagicMock(side_effect=lambda operation, **_kwargs: operation())
    service._enqueue_analysis = MagicMock()

    result = service.reject(
        actor=MagicMock(),
        work_order_id=record.id,
        request_id="reject-redispatch",
        reason="skill mismatch",
        version=3,
    )

    assert result is record
    service._execute.assert_called_once()
    service._enqueue_analysis.assert_called_once_with(record.id, "reject-redispatch", version=record.version)


def test_final_analysis_skip_attempts_redispatch_without_provider_recall() -> None:
    service = AnalysisService.__new__(AnalysisService)
    service._prepare = MagicMock(
        return_value=SimpleNamespace(skipped=True, analysis_id=19)
    )
    analysis = SimpleNamespace(id=19)
    service._ai = MagicMock()
    service._retry_auto_dispatch_from_existing = MagicMock(return_value="ASSIGNED")
    service._ai.get_current_analysis.side_effect = lambda _work_order_id: (
        analysis
        if service._retry_auto_dispatch_from_existing.called
        else pytest.fail("analysis read must not autobegin before redispatch")
    )
    service._call_providers = MagicMock()

    result = service.analyze_work_order(
        work_order_id=41,
        request_id="redispatch-existing-analysis",
    )

    assert result is analysis
    service._retry_auto_dispatch_from_existing.assert_called_once_with(
        work_order_id=41,
        analysis_id=19,
        request_id="redispatch-existing-analysis",
    )
    service._call_providers.assert_not_called()


def test_stored_completed_analysis_can_be_mapped_for_redispatch() -> None:
    service = AnalysisService.__new__(AnalysisService)
    service._settings = Settings(_env_file=None, ai_confidence_threshold=0.7)
    analysis = SimpleNamespace(
        status=AnalysisStatus.COMPLETED.value,
        category="ELECTRICAL",
        subcategory="outlet",
        severity="HIGH",
        priority="HIGH",
        summary="outlet is sparking",
        risk="electric",
        required_role="STAFF",
        required_skill="ELECTRICAL",
        confidence=Decimal("0.900"),
    )

    mapped = service._mapped_from_stored_analysis(analysis)

    assert mapped is not None
    assert mapped.required_skill == "ELECTRICAL"
    assert mapped.required_duty == "MAINTENANCE"
    assert mapped.low_confidence is False


def test_recently_rejecting_employee_is_filtered_before_ranking() -> None:
    settings = Settings(
        _env_file=None,
        ai_auto_assign=True,
        dispatch_reject_cooldown_seconds=1800,
    )
    service = AutoDispatchService(MagicMock(), settings=settings)
    service._ai = MagicMock()
    service._work_orders = MagicMock()
    record = _pending_order()
    rejected_at = datetime.now(UTC) - timedelta(minutes=1)
    service._work_orders.list_assignments.return_value = (
        SimpleNamespace(
            employee_id=7,
            status=AssignmentStatus.REJECTED.value,
            ended_at=rejected_at,
            timeout_at=None,
        ),
        SimpleNamespace(
            employee_id=9,
            status=AssignmentStatus.REASSIGNED.value,
            ended_at=rejected_at,
            timeout_at=rejected_at,
        ),
    )
    service._work_orders.get_property.return_value = SimpleNamespace(
        timezone="Asia/Shanghai"
    )
    employees = (
        SimpleNamespace(id=7, user_id=17, status=EmployeeStatus.ACTIVE.value, max_capacity=8),
        SimpleNamespace(id=8, user_id=18, status=EmployeeStatus.ACTIVE.value, max_capacity=8),
        SimpleNamespace(id=9, user_id=19, status=EmployeeStatus.ACTIVE.value, max_capacity=8),
    )
    service._ai.list_property_employees.return_value = employees
    service._work_orders.employee_global_occupancy.return_value = 0
    service._work_orders.employee_last_assignment_id.return_value = 0
    service._ai.get_building_id.return_value = 9
    service._ai.get_effective_property.return_value = SimpleNamespace(id=1)
    service._ai.get_daily_status.return_value = SimpleNamespace(
        current_load=0,
        on_duty=AttendanceOnDuty.ON_DUTY.value,
    )
    service._ai.get_attendance.return_value = SimpleNamespace(
        check_in_at=datetime.now(UTC) - timedelta(hours=1)
    )
    service._ai.list_employee_role_codes.return_value = frozenset({"STAFF"})
    service._ai.get_dispatch_history.return_value = (60.0, 1.0, 5.0)
    service._ai.list_employee_duties.return_value = frozenset({"MAINTENANCE"})
    service._ai.list_employee_skills.return_value = frozenset({"ELECTRICAL"})
    service._ai.get_user_status.return_value = "ACTIVE"
    service._ai.count_same_building_history.return_value = 0

    mapped = AnalysisService.__new__(AnalysisService)
    mapped._settings = settings
    analysis = SimpleNamespace(
        category="ELECTRICAL",
        subcategory="outlet",
        severity="HIGH",
        priority="HIGH",
        summary="outlet is sparking",
        risk="electric",
        required_role="STAFF",
        required_skill="ELECTRICAL",
        confidence=Decimal("0.900"),
    )
    parsed = mapped._mapped_from_stored_analysis(analysis)
    assert parsed is not None

    ranked, snapshot = service._evaluate_live_candidates(record, parsed)

    assert [candidate.employee_id for candidate, _score, _reasons in ranked] == [8]
    by_employee = {item["employee_id"]: item for item in snapshot}
    assert by_employee[7]["eligible"] is False
    assert by_employee[9]["eligible"] is False
    assert by_employee[7]["filter_reason"] == "RECENTLY_REJECTED"
    assert by_employee[8]["eligible"] is True


def test_feedback_cannot_clear_escalation_even_with_decide_permission() -> None:
    service = WorkOrderService.__new__(WorkOrderService)
    current = MagicMock()
    current.has_permission.return_value = True
    record = SimpleNamespace(
        id=41,
        status="PROCESSING",
        process_reason="ESCALATED",
    )
    service._lock_assignee_action = MagicMock(
        return_value=(current, record, MagicMock(), MagicMock())
    )
    service._repository = MagicMock()
    service._attach_media = MagicMock()
    service._property_scope = MagicMock()

    with pytest.raises(AppError) as captured:
        service._feedback(
            actor=MagicMock(),
            work_order_id=record.id,
            request_id="staff-self-clear",
            text=None,
            process_reason=ProcessReason.NONE.value,
            file_ids=(),
            taken_phase=None,
            version=None,
        )

    assert captured.value.code == WorkOrderErrorCode.INVALID_TRANSITION


def test_supervisor_continue_requires_plan_and_clears_exception(monkeypatch) -> None:
    service = WorkOrderService.__new__(WorkOrderService)
    fixed_now = datetime(2026, 9, 19, 19, 0, tzinfo=UTC)
    service._clock = lambda: fixed_now
    current = SimpleNamespace(user_id=22)
    record = SimpleNamespace(
        id=41,
        tenant_id=1,
        property_id=2,
        reporter_user_id=7,
        status="PROCESSING",
        process_reason="ESCALATED",
        version=7,
    )
    service._reload_actor = MagicMock(return_value=current)
    service._lock_work_order = MagicMock(return_value=record)
    service._assert_version = MagicMock()
    service._property_scope = MagicMock()
    service._actor_type = MagicMock()
    service._apply_transition = MagicMock()
    service._append_audit = MagicMock()
    service._resume_receipted_process = MagicMock()
    service._repository = MagicMock()
    service._repository.get_active_assignee_user_id.return_value = 23
    service._notify_users = MagicMock()
    monkeypatch.setattr(
        "app.service.work_order.RbacService.require_permission",
        lambda *_args, **_kwargs: None,
    )

    with pytest.raises(AppError) as captured:
        service._decide(
            actor=MagicMock(),
            work_order_id=record.id,
            decision="CONTINUE",
            request_id="continue-without-plan",
            employee_id=None,
            reason=None,
            version=record.version,
        )
    assert captured.value.code == WorkOrderErrorCode.INVALID_INPUT

    result = service._decide(
        actor=MagicMock(),
        work_order_id=record.id,
        decision="CONTINUE",
        request_id="continue-with-plan",
        employee_id=None,
        reason="Use the safe internal repair plan",
        version=record.version,
    )

    assert result is record
    transition = service._apply_transition.call_args.kwargs
    assert transition["process_reason"] is ProcessReason.NONE
    assert transition["reason_detail"] == "Use the safe internal repair plan"
    service._resume_receipted_process.assert_called_once_with(
        record,
        now=fixed_now,
    )
    service._notify_users.assert_called_once_with(
        record,
        user_ids=(7, 23),
        exclude_user_ids=(22,),
        title="工单处理方案已更新",
        body="主管已更新工单处理方案，请查看详情",
    )


@pytest.mark.parametrize("employee_id", [None, 27])
def test_supervisor_reassign_requires_reason_for_both_branches(
    monkeypatch, employee_id: int | None
) -> None:
    service = WorkOrderService.__new__(WorkOrderService)
    service._reload_actor = MagicMock(return_value=SimpleNamespace(user_id=22))
    record = SimpleNamespace(
        id=41,
        tenant_id=1,
        property_id=2,
        status="PROCESSING",
        process_reason="INTERNAL_UNRESOLVED",
        version=7,
    )
    service._lock_work_order = MagicMock(return_value=record)
    service._assert_version = MagicMock()
    service._property_scope = MagicMock()
    service._assign = MagicMock()
    service._end_current_process = MagicMock()
    service._end_active_assignment = MagicMock()
    monkeypatch.setattr(
        "app.service.work_order.RbacService.require_permission",
        lambda *_args, **_kwargs: None,
    )

    with pytest.raises(AppError) as captured:
        service._decide(
            actor=MagicMock(),
            work_order_id=record.id,
            decision="REASSIGN",
            request_id="reassign-without-reason",
            employee_id=employee_id,
            reason="  ",
            version=record.version,
        )

    assert captured.value.code == WorkOrderErrorCode.INVALID_INPUT
    service._assign.assert_not_called()
    service._end_current_process.assert_not_called()


def test_continue_after_unresolved_receipt_opens_new_same_assignee_cycle() -> None:
    service = WorkOrderService.__new__(WorkOrderService)
    service._repository = MagicMock()
    service._session = MagicMock()
    ended = datetime(2026, 9, 19, 18, 0, tzinfo=UTC)
    process = SimpleNamespace(id=9, cycle_no=2, ended_at=ended)
    service._repository.get_current_process.return_value = process
    service._repository.get_receipt_for_process.return_value = SimpleNamespace(id=10)
    service._repository.get_active_assignment_for_update.return_value = SimpleNamespace(
        employee_id=27
    )
    record = SimpleNamespace(id=41, tenant_id=1, property_id=2)
    now = datetime(2026, 9, 19, 19, 0, tzinfo=UTC)

    service._resume_receipted_process(record, now=now)

    created = service._repository.add_process.call_args.args[0]
    assert created.cycle_no == 3
    assert created.assignee_id == 27
    assert created.started_at == now


def test_reassignment_closes_open_process_cycle() -> None:
    service = WorkOrderService.__new__(WorkOrderService)
    service._repository = MagicMock()
    service._session = MagicMock()
    process = SimpleNamespace(ended_at=None)
    service._repository.get_current_process.return_value = process
    record = SimpleNamespace(id=41)
    now = datetime(2026, 9, 19, 19, 0, tzinfo=UTC)

    service._end_current_process(record, now=now)

    assert process.ended_at == now
    service._session.flush.assert_called_once()


def test_staff_page_wires_reject_escalate_and_unresolved_receipt() -> None:
    source = (
        REPO_ROOT / "miniprogram/pages/staff-orders/detail.js"
    ).read_text(encoding="utf-8")
    template = (
        REPO_ROOT / "miniprogram/pages/staff-orders/detail.wxml"
    ).read_text(encoding="utf-8")

    assert ".rejectWorkOrder(" in source
    # 2026-10 起「无法处理 → 上报主管」改成独立页面承接（detail 只负责跳转，
    # pages/staff-orders/detail.js:251 navigateTo(escalate)），submit 在 escalate.js:52。
    assert "/pages/staff-orders/escalate?" in source
    escalate = (
        REPO_ROOT / "miniprogram/pages/staff-orders/escalate.js"
    ).read_text(encoding="utf-8")
    assert ".escalateWorkOrder(" in escalate
    assert "unresolved_reason" in source
    assert "wx.getImageInfo" in source
    assert "拒单并自动改派" in template
    assert "提交未解决回执并上报" in template
    # 「提交主管处理」随上报流程迁到独立页面（escalate.wxml:11）。
    assert "提交主管处理" in (
        REPO_ROOT / "miniprogram/pages/staff-orders/escalate.wxml"
    ).read_text(encoding="utf-8")


def test_supervisor_page_wires_all_frozen_exception_decisions() -> None:
    source = (
        REPO_ROOT / "miniprogram/pages/supervisor/queue.js"
    ).read_text(encoding="utf-8")
    template = (
        REPO_ROOT / "miniprogram/pages/supervisor/queue.wxml"
    ).read_text(encoding="utf-8")

    assert ".decideWorkOrder(" in source
    assert 'this._dispatch("reassignWorkOrder")' in source
    for decision in (
        "WAIT_PARTS",
        "WAIT_OWNER",
        "EXTERNAL_COORDINATION",
        "CONTINUE",
    ):
        assert decision in source or decision in template
    assert "物业协调第三方" in template
    assert "人工后备派单" in template
