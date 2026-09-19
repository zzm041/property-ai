"""Rule auto-dispatch: flag, risk fallback, schema gate, and assign-time selection."""

from types import SimpleNamespace
from unittest.mock import MagicMock

from app.core.config import Settings
from app.domain.ai import AiRisk, DispatchCandidate, MappedAnalysis
from app.domain.work_order import WorkOrderCategory, WorkOrderPriority, WorkOrderSeverity
from app.model.work_order import WorkOrder
from app.service.auto_dispatch import AutoDispatchService, assignment_schema_ready


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
    monkeypatch.setattr(service, "_rank_live_candidates", lambda *_args, **_kwargs: ())
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
        "_rank_live_candidates",
        lambda *_args, **_kwargs: ((winner, 0.82, ("SKILL_MATCH",)),),
    )
    monkeypatch.setattr(service, "_commit_auto_assignment", lambda **_kwargs: "ASSIGNED")
    assert service.maybe_assign(
        work_order=_pending_order(),
        mapped=_mapped(risk=AiRisk.ELECTRIC),
        request_id="r-ok",
    ) == "ASSIGNED"
