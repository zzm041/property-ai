"""Rule auto-dispatch: flag, risk fallback, schema gate, and assign-time selection."""

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.core.config import Settings
from app.api.v1.supervisor_ai import get_analysis_service
from app.api.v1.work_orders import get_work_order_service
from app.domain.ai import AiRisk, DispatchCandidate, MappedAnalysis
from app.domain.work_order import WorkOrderCategory, WorkOrderPriority, WorkOrderSeverity
from app.model.work_order import WorkOrder
from app.schema.work_order import WorkOrderAssignmentData
from app.service.analysis import _STAFF_ROLES as ANALYSIS_STAFF_ROLES
from app.service.auto_dispatch import (
    _STAFF_ROLES as AUTO_DISPATCH_STAFF_ROLES,
    AutoDispatchService,
    assignment_schema_ready,
)
from app.service.work_order import WorkOrderService

REPO_ROOT = Path(__file__).resolve().parents[3]


def _mapped(*, risk: AiRisk = AiRisk.NONE, low_confidence: bool = False) -> MappedAnalysis:
    return MappedAnalysis(
        category=WorkOrderCategory.ELECTRICAL,
        subcategory="outlet",
        severity=WorkOrderSeverity.HIGH,
        priority=WorkOrderPriority.HIGH,
        summary="test",
        risk=risk,
        required_role="STAFF",
        required_skill="ELECTRICAL",
        required_duty="MAINTENANCE",
        confidence=0.9,
        low_confidence=low_confidence,
    )


def _pending_order() -> WorkOrder:
    record = WorkOrder()
    record.id = 11
    record.tenant_id = 1
    record.property_id = 1
    record.house_id = 1
    record.status = "PENDING_ASSIGNMENT"
    record.current_assignment_id = None
    record.version = 1
    record.process_reason = "NONE"
    return record


def _candidate() -> DispatchCandidate:
    return DispatchCandidate(
        employee_id=7,
        duty_codes=frozenset({"MAINTENANCE"}),
        skill_codes=frozenset({"ELECTRICAL"}),
        current_load=0,
        max_capacity=8,
        on_duty=True,
        employee_active=True,
        property_effective=True,
        has_staff_role=True,
    )


def test_assignment_schema_ready_false_without_session() -> None:
    assert assignment_schema_ready(None) is False


def test_api_service_factories_preserve_application_settings() -> None:
    settings = Settings(
        _env_file=None,
        app_env="test",
        ai_auto_assign=True,
        ai_enqueue_on_create=True,
    )
    session = MagicMock()
    assert get_work_order_service(session, settings)._settings is settings
    assert get_analysis_service(session, settings)._settings is settings


def test_staff_or_higher_roles_are_consistent_in_analysis_and_assignment() -> None:
    expected = frozenset({"STAFF", "SUPERVISOR", "PROPERTY_ADMIN", "TENANT_ADMIN"})

    assert ANALYSIS_STAFF_ROLES == expected
    assert AUTO_DISPATCH_STAFF_ROLES == expected


def test_idempotent_create_replay_enqueues_analysis_only_for_new_order() -> None:
    service = WorkOrderService.__new__(WorkOrderService)
    record = _pending_order()
    service._execute = lambda operation: operation()
    service._create_work_order = MagicMock(
        side_effect=((record, True), (record, False))
    )
    service._enqueue_analysis = MagicMock()
    arguments = {
        "actor": MagicMock(),
        "house_id": 1,
        "request_id": "request-idempotent-create",
        "idempotency_key": "idempotent-create-key",
        "contact_phone": "+8613800000123",
        "text": "socket is sparking",
    }

    first = service.create_work_order(**arguments)
    replay = service.create_work_order(**arguments)

    assert first is record
    assert replay is record
    service._enqueue_analysis.assert_called_once_with(
        record.id,
        "request-idempotent-create",
    )


def test_auto_assignment_response_allows_no_human_actor() -> None:
    assignment = WorkOrderAssignmentData(
        id=3,
        employee_id=7,
        assigned_by_user_id=None,
        status="ACTIVE",
        assigned_at=datetime(2026, 9, 19, tzinfo=UTC),
        accept_at=None,
        ended_at=None,
        reason="AUTO_DISPATCH",
    )

    assert assignment.assigned_by_user_id is None


def test_resident_detail_surfaces_async_auto_dispatch() -> None:
    source = (REPO_ROOT / "miniprogram/pages/work-orders/detail.js").read_text(
        encoding="utf-8"
    )
    template = (REPO_ROOT / "miniprogram/pages/work-orders/detail.wxml").read_text(
        encoding="utf-8"
    )

    assert 'status === "PENDING_ANALYSIS"' in source
    assert 'status === "PENDING_ASSIGNMENT"' in source
    assert 'currentAssignment.reason === "AUTO_DISPATCH"' in source
    assert "系统已自动派单" in template


def test_low_confidence_does_not_assign() -> None:
    settings = Settings(_env_file=None, ai_auto_assign=True)
    assert AutoDispatchService(None, settings=settings).maybe_assign(
        work_order=_pending_order(),
        mapped=_mapped(low_confidence=True),
        request_id="r-low",
    ) == "LOW_CONFIDENCE"


def test_already_assigned_is_idempotent(monkeypatch) -> None:
    settings = Settings(_env_file=None, ai_auto_assign=True)
    record = _pending_order()
    record.status = "ASSIGNED"
    record.current_assignment_id = 99
    session = MagicMock()
    monkeypatch.setattr(
        "app.service.auto_dispatch.assignment_schema_ready",
        lambda _session: True,
    )
    assert AutoDispatchService(session, settings=settings).maybe_assign(
        work_order=record,
        mapped=_mapped(risk=AiRisk.ELECTRIC),
        request_id="r-dup",
    ) == "ALREADY_ASSIGNED"


def test_stale_status_is_not_overwritten(monkeypatch) -> None:
    settings = Settings(_env_file=None, ai_auto_assign=True)
    record = _pending_order()
    record.status = "CANCELLED"
    session = MagicMock()
    monkeypatch.setattr(
        "app.service.auto_dispatch.assignment_schema_ready",
        lambda _session: True,
    )
    assert AutoDispatchService(session, settings=settings).maybe_assign(
        work_order=record,
        mapped=_mapped(),
        request_id="r-stale",
    ) == "STALE_STATUS"


def test_no_eligible_candidate_stays_pending(monkeypatch) -> None:
    settings = Settings(_env_file=None, ai_auto_assign=True)
    session = MagicMock()
    service = AutoDispatchService(session, settings=settings)
    service._work_orders = MagicMock()
    service._work_orders.get_active_assignment.return_value = None
    monkeypatch.setattr(
        "app.service.auto_dispatch.assignment_schema_ready",
        lambda _session: True,
    )
    monkeypatch.setattr(
        service,
        "_evaluate_live_candidates",
        lambda *_args, **_kwargs: ((), ()),
    )
    assert service.maybe_assign(
        work_order=_pending_order(),
        mapped=_mapped(),
        request_id="r-none",
    ) == "NO_ELIGIBLE"


def test_auto_dispatch_calls_commit_for_winner(monkeypatch) -> None:
    settings = Settings(_env_file=None, ai_auto_assign=True)
    session = MagicMock()
    session.begin_nested.return_value = SimpleNamespace(
        commit=lambda: None,
        rollback=lambda: None,
    )
    service = AutoDispatchService(session, settings=settings)
    service._work_orders = MagicMock()
    service._work_orders.get_active_assignment.return_value = None
    winner = _candidate()
    monkeypatch.setattr(
        "app.service.auto_dispatch.assignment_schema_ready",
        lambda _session: True,
    )
    monkeypatch.setattr(
        service,
        "_evaluate_live_candidates",
        lambda *_args, **_kwargs: (
            ((winner, 0.82, ("SKILL_MATCH",)),),
            ({"employee_id": winner.employee_id, "eligible": True},),
        ),
    )
    monkeypatch.setattr(service, "_commit_auto_assignment", lambda **_kwargs: "ASSIGNED")
    assert service.maybe_assign(
        work_order=_pending_order(),
        mapped=_mapped(risk=AiRisk.ELECTRIC),
        request_id="r-ok",
    ) == "ASSIGNED"


def test_auto_dispatch_tries_next_ranked_candidate_when_winner_turns_ineligible(
    monkeypatch,
) -> None:
    settings = Settings(_env_file=None, ai_auto_assign=True)
    session = MagicMock()
    service = AutoDispatchService(session, settings=settings)
    service._work_orders = MagicMock()
    service._work_orders.get_active_assignment.return_value = None
    first = _candidate()
    second = DispatchCandidate(
        employee_id=8,
        duty_codes=first.duty_codes,
        skill_codes=first.skill_codes,
        current_load=1,
        max_capacity=8,
        on_duty=True,
        employee_active=True,
        property_effective=True,
        has_staff_role=True,
    )
    monkeypatch.setattr(
        "app.service.auto_dispatch.assignment_schema_ready",
        lambda _session: True,
    )
    monkeypatch.setattr(
        service,
        "_evaluate_live_candidates",
        lambda *_args, **_kwargs: (
            (
                (first, 0.9, ("SKILL_MATCH",)),
                (second, 0.8, ("SKILL_MATCH",)),
            ),
            (),
        ),
    )
    commit = MagicMock(side_effect=("NO_ELIGIBLE", "ASSIGNED"))
    monkeypatch.setattr(service, "_commit_auto_assignment", commit)
    assert service.maybe_assign(
        work_order=_pending_order(),
        mapped=_mapped(),
        request_id="r-next",
    ) == "ASSIGNED"
    assert [call.kwargs["winner"].employee_id for call in commit.call_args_list] == [7, 8]


def test_failed_savepoint_restores_only_outer_notification_intents(monkeypatch) -> None:
    settings = Settings(_env_file=None, ai_auto_assign=True)
    session = MagicMock()
    session.info = {
        "pending_notification_deliveries": [71],
        "notification_settings": settings,
    }
    nested = MagicMock()
    session.begin_nested.return_value = nested
    service = AutoDispatchService(session, settings=settings)
    service._work_orders = MagicMock()
    service._work_orders.get_active_assignment.return_value = None
    winner = _candidate()
    monkeypatch.setattr(
        "app.service.auto_dispatch.assignment_schema_ready",
        lambda _session: True,
    )
    monkeypatch.setattr(
        service,
        "_evaluate_live_candidates",
        lambda *_args, **_kwargs: (((winner, 0.9, ("SKILL_MATCH",)),), ()),
    )

    def fail_after_notification(**_kwargs):
        session.info["pending_notification_deliveries"].append(72)
        raise RuntimeError("candidate savepoint failed")

    monkeypatch.setattr(service, "_commit_auto_assignment", fail_after_notification)

    assert service.maybe_assign(
        work_order=_pending_order(),
        mapped=_mapped(),
        request_id="r-savepoint-failure",
    ) == "ASSIGN_FAILED"
    nested.rollback.assert_called_once()
    assert session.info["pending_notification_deliveries"] == [71]
    assert session.info["notification_settings"] is settings


def test_incident_follower_reuses_full_primary_without_duplicate_load(monkeypatch) -> None:
    settings = Settings(_env_file=None, app_env="test", ai_auto_assign=True)
    session = MagicMock()
    service = AutoDispatchService(session, settings=settings)
    service._work_orders = MagicMock()
    service._employees = MagicMock()
    service._ai = MagicMock()
    service._audit = MagicMock()
    service._insert_auto_assignment = MagicMock(return_value=91)
    record = _pending_order()
    record.incident_id = 501
    employee = SimpleNamespace(
        id=7,
        tenant_id=record.tenant_id,
        user_id=17,
        status="ACTIVE",
        max_capacity=2,
    )
    daily = SimpleNamespace(current_load=2, on_duty="ON_DUTY")
    incident = SimpleNamespace(
        id=501,
        primary_work_order_id=10,
        primary_employee_id=7,
    )
    winner = DispatchCandidate(
        employee_id=7,
        duty_codes=frozenset({"MAINTENANCE"}),
        skill_codes=frozenset({"ELECTRICAL"}),
        current_load=2,
        max_capacity=2,
        on_duty=True,
        employee_active=True,
        property_effective=True,
        has_staff_role=True,
        primary_incident_handler=True,
        incident_follow=True,
    )
    service._work_orders.list_assignments.return_value = ()
    service._work_orders.get_property.return_value = SimpleNamespace(
        timezone="Asia/Shanghai"
    )
    service._work_orders.get_sla_clock_for_update.return_value = None
    service._employees.get_employee_for_update.return_value = employee
    service._work_orders.employee_global_occupancy.return_value = 2
    service._employees.get_daily_status_for_update.return_value = daily
    service._ai.get_incident_for_update.return_value = incident
    service._ai.get_attendance.return_value = SimpleNamespace(
        check_in_at=datetime.now(UTC)
    )
    service._ai.get_effective_property.return_value = SimpleNamespace(id=1)
    service._ai.list_employee_role_codes.return_value = frozenset({"STAFF"})
    service._ai.list_employee_duties.return_value = frozenset({"MAINTENANCE"})
    service._ai.list_employee_skills.return_value = frozenset({"ELECTRICAL"})
    service._ai.get_user_status.return_value = "ACTIVE"
    service._ai.list_recommendations.return_value = ()
    notifier = MagicMock()
    monkeypatch.setattr(
        "app.service.auto_dispatch.NotificationService",
        MagicMock(return_value=notifier),
    )

    outcome = service._commit_auto_assignment(
        record=record,
        mapped=_mapped(),
        winner=winner,
        score=0.9,
        reasons=("INCIDENT_FOLLOW",),
        filter_snapshot=(),
        request_id="incident-follow-11",
    )

    assert outcome == "ASSIGNED"
    assert daily.current_load == 2
    payload = service._insert_auto_assignment.call_args.kwargs["payload"]
    assert payload["incident_id"] == 501
    assert payload["incident_follow"] is True
    assert payload["incident_capacity_owner"] is False
    log = service._work_orders.add_log.call_args.args[0]
    assert log.payload_json["reason_code"] == "AUTO_INCIDENT_FOLLOW"
    assert incident.primary_work_order_id == 10
    assert incident.primary_employee_id == 7
    notifier.enqueue.assert_called_once()
