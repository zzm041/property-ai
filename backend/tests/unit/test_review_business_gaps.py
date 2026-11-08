"""Review findings: synthetic services only; no database or external calls."""
from types import SimpleNamespace as NS
from unittest.mock import MagicMock

import pytest

from app.core.errors import AppError
from app.service.work_order import WorkOrderService
from test_incident_management import _service, _staff, _manager, _incident
from test_work_order_live_eligibility import ctx
from test_manual_dispatch_capability import setup


@pytest.fixture
def incident_case():
    service, record = _service(), _incident()
    service._work_orders.get_property.return_value = NS(id=10, tenant_id=1)
    service._employees.get_employee_for_user.return_value = NS(id=31, tenant_id=1, status="ACTIVE")
    service._employees.find_effective_assignment.return_value = object()
    service._ai.get_incident.return_value = record
    service._ai.list_incidents_for_participant.return_value = (record,)
    service._ai.is_incident_participant.return_value = True
    return service, record


@pytest.mark.parametrize("failure", ["employee_missing", "employee_inactive", "tenant", "expired_relation", "future_relation"])
def test_incident_current_employee_gate_on_list_and_detail(incident_case, failure):
    service, record = incident_case
    if failure == "employee_missing": service._employees.get_employee_for_user.return_value = None
    elif failure == "employee_inactive": service._employees.get_employee_for_user.return_value.status = "INACTIVE"
    elif failure == "tenant": service._employees.get_employee_for_user.return_value.tenant_id = 99
    else: service._employees.find_effective_assignment.return_value = None
    assert service.list_incidents(actor=_staff(), property_id=10) == ()
    service._ai.list_incidents_for_participant.assert_not_called()
    with pytest.raises(AppError) as error: service.get_incident(actor=_staff(), incident_id=record.id)
    assert error.value.status_code == 403


def test_incident_valid_participant_and_management_exception(incident_case):
    service, record = incident_case
    assert service.list_incidents(actor=_staff(), property_id=10) == (record,)
    assert service.get_incident(actor=_staff(), incident_id=record.id) is record
    assert service._employees.find_effective_assignment.call_args.kwargs["property_id"] == 10
    service._ai.is_incident_participant.return_value = False
    with pytest.raises(AppError): service.get_incident(actor=_staff(), incident_id=record.id)
    service._employees.get_employee_for_user.return_value = None
    assert service.get_incident(actor=_manager(), incident_id=record.id) is record


@pytest.mark.parametrize("category", ["PLUMBING", "ELECTRICAL", "HVAC"])
def test_manual_ordinary_repair_requires_global_idle_even_below_capacity(ctx, category):
    setup(ctx, category=category)
    ctx.service._repository.employee_global_occupancy.return_value = 1
    assert ctx.employee.max_capacity == 2
    assert ctx.service._eligibility_error(ctx.record, ctx.employee) is not None
    # Merely attaching an incident id is not AUTO follow provenance.
    ctx.record.incident_id = 55
    assert ctx.service._eligibility_error(ctx.record, ctx.employee) is not None
    # Existing reservation retained by reopen must not be counted twice.
    assert ctx.service._eligibility_error(ctx.record, ctx.employee, ignore_capacity=True) is None
    ctx.service._repository.employee_global_occupancy.return_value = 0
    assert ctx.service._eligibility_error(ctx.record, ctx.employee) is None


def test_nonordinary_work_keeps_capacity_and_restricted_skill_rules(ctx):
    setup(ctx, "ELECTRICAL", "HIGH_VOLTAGE")
    ctx.service._repository.employee_global_occupancy.return_value = 1
    ctx.service._analysis.list_employee_skills.return_value = frozenset({"HIGH_VOLTAGE"})
    assert ctx.service._eligibility_error(ctx.record, ctx.employee) is None
    ctx.service._repository.employee_global_occupancy.return_value = 2
    assert ctx.service._eligibility_error(ctx.record, ctx.employee) is not None


@pytest.fixture
def feedback_case():
    service = WorkOrderService.__new__(WorkOrderService)
    actor = _staff()
    record = NS(id=41, tenant_id=1, property_id=10, reporter_user_id=8,
                status="PROCESSING", process_reason="NONE", version=1)
    content = NS(extra_notes="existing")
    service._session, service._repository = MagicMock(), MagicMock()
    service._repository.get_content.return_value = content
    service._repository.list_sla_management_user_ids.return_value = (19,)
    service._lock_assignee_action = MagicMock(return_value=(actor, record, None, None))
    service._append_audit, service._notify_users, service._attach_media = MagicMock(), MagicMock(), MagicMock()
    return NS(service=service, record=record, actor=actor, content=content,
              feedback=lambda reason: service._feedback(actor=actor, work_order_id=41, request_id="synthetic",
                  text="new progress", process_reason=reason, file_ids=(), taken_phase=None, version=1))


@pytest.mark.parametrize("reason", ["INTERNAL_UNRESOLVED", "ESCALATED", "WAITING_PARTS", "WAITING_OWNER"])
def test_feedback_cannot_enter_escalation_without_dedicated_notify_path(feedback_case, reason):
    c = feedback_case
    with pytest.raises(AppError) as error: c.feedback(reason)
    assert error.value.status_code == 409
    assert c.record.process_reason == "NONE" and c.content.extra_notes == "existing"
    c.service._attach_media.assert_not_called()
    c.service._repository.add_log.assert_not_called()


@pytest.mark.parametrize("reason", [None, "ESCALATED"])
def test_feedback_on_existing_escalation_can_append_without_duplicate_notice(feedback_case, reason):
    c = feedback_case
    c.record.process_reason = "ESCALATED"
    assert c.feedback(reason).process_reason == "ESCALATED"
    assert "new progress" in c.content.extra_notes
    c.service._notify_users.assert_not_called()


def test_each_progress_log_keeps_only_its_own_text_and_actor(feedback_case):
    c = feedback_case
    for text in ('  first internal arrangement  ', 'second internal update'):
        c.service._feedback(actor=c.actor, work_order_id=41, request_id='synthetic',
            text=text, process_reason=None, file_ids=(), taken_phase=None, version=c.record.version)
    logs = [call.args[0] for call in c.service._repository.add_log.call_args_list]
    assert [log.payload_json.get('reason') for log in logs] == ['first internal arrangement', 'second internal update']
    assert all(log.actor_user_id == c.actor.user_id and log.event_code == 'PROCESS_UPDATE' for log in logs)
    assert c.content.extra_notes == 'existing\nfirst internal arrangement\nsecond internal update'


def test_progress_without_text_does_not_invent_history(feedback_case):
    c = feedback_case
    c.service._feedback(actor=c.actor, work_order_id=41, request_id='synthetic', text=None,
        process_reason=None, file_ids=(), taken_phase=None, version=1)
    assert 'reason' not in c.service._repository.add_log.call_args.args[0].payload_json
    assert c.content.extra_notes == 'existing'


@pytest.mark.parametrize("reason", ["NONE", "WAITING_OWNER"])
def test_feedback_cannot_resume_or_change_existing_wait_decision(feedback_case, reason):
    c = feedback_case
    c.record.process_reason = "WAITING_PARTS"
    with pytest.raises(AppError): c.feedback(reason)
    assert c.content.extra_notes == "existing"


def test_dedicated_escalation_notifies_resident_and_managers(feedback_case):
    c = feedback_case
    c.service._escalate(actor=c.actor, work_order_id=41, request_id="synthetic", process_reason="ESCALATED",
                       reason="synthetic needs coordination", version=1)
    assert c.record.process_reason == "ESCALATED"
    assert c.service._notify_users.call_args.kwargs["user_ids"] == (8, 19)
    assert c.service._repository.add_log.call_count == 1
