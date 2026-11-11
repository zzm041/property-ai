"""Owner policy v2; software-only, no service/database/model calls."""
from contextlib import nullcontext
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy.dialects import mysql

from app.core.config import Settings
from app.core.logging import redact_text, redact_value
from app.domain.ai import (
    DispatchCandidate, FilterReason, WorkOrderAnalysisOutput,
    hard_filter_candidate, map_analysis_output, rank_candidates,
)
from app.model.work_order import WorkOrder
from app.repository.ai import AiRepository
from app.repository.work_order import WorkOrderRepository
from app.service.analysis import AnalysisService
from app.service.auto_dispatch import AutoDispatchService


def candidate(**kwargs):
    values = dict(employee_id=17, duty_codes=frozenset({'MAINTENANCE'}),
                  skill_codes=frozenset(), current_load=0, max_capacity=3,
                  on_duty=True, employee_active=True, property_effective=True,
                  has_staff_role=True)
    return DispatchCandidate(**(values | kwargs))


def mapped(category='electrical', skill='maintenance', **kwargs):
    data = dict(category=category, subcategory='本次现象', severity='low', priority='p3',
                summary='插座不通电；水龙头滴水', risk='none', required_role='STAFF',
                required_skill=skill, confidence=.9)
    return map_analysis_output(WorkOrderAnalysisOutput(**(data | kwargs)))


@pytest.mark.parametrize('category', ['water_supply', 'electrical', 'hvac', 'other'])
def test_one_real_maintenance_member_without_trade_tags_can_take_ordinary_work(category):
    analysis = mapped(category)
    assert not analysis.low_confidence
    result = rank_candidates((candidate(),), required_duty=analysis.required_duty,
                             required_skill=analysis.required_skill)
    assert [x[0].employee_id for x in result] == [17]
    assert 'MAINTENANCE_GROUP' in result[0][2]


@pytest.mark.parametrize('skill', ['PLUMBING', 'ELECTRICAL', 'HVAC'])
def test_existing_trade_analysis_is_not_a_hidden_exclusive_skill_gate(skill):
    assert hard_filter_candidate(candidate(), required_duty='MAINTENANCE', required_skill=skill) is None


@pytest.mark.parametrize('changes,reason', [
    ({'duty_codes': frozenset({'CLEANING', 'SECURITY'})}, FilterReason.DUTY_MISMATCH),
    ({'property_effective': False}, FilterReason.PROPERTY_NOT_EFFECTIVE),
    ({'on_duty': False}, FilterReason.NOT_ON_DUTY),
    ({'checked_in': False}, FilterReason.NOT_CHECKED_IN),
    ({'employee_active': False}, FilterReason.NOT_ACTIVE),
    ({'user_active': False}, FilterReason.USER_NOT_ACTIVE),
    ({'has_staff_role': False}, FilterReason.NO_STAFF_ROLE),
    ({'current_load': 1}, FilterReason.BUSY),
    ({'current_load': 3}, FilterReason.CAPACITY_FULL),
])
def test_no_qualification_or_busy_slot_is_not_available(changes, reason):
    assert hard_filter_candidate(candidate(**changes), required_duty='MAINTENANCE', required_skill='MAINTENANCE') == reason


def test_fair_rotation_uses_persistent_assignment_turn_not_input_order_or_skill():
    people = [candidate(employee_id=n) for n in (91, 32, 58)]
    winners = []
    for turn in range(1, 7):
        result = rank_candidates(tuple(reversed(people)), required_duty='MAINTENANCE', required_skill='HVAC')
        winner = result[0][0]
        winners.append(winner.employee_id)
        people = [replace(p, last_assignment_id=turn) if p == winner else p for p in people]
    assert winners == [32, 58, 91, 32, 58, 91]


@pytest.mark.parametrize('duty,skill', [('CLEANING', 'CLEANING'), ('SECURITY', 'SECURITY'), ('MAINTENANCE', 'LIFT_LICENSE')])
def test_other_duties_and_explicit_restricted_skill_stay_hard_constraints(duty, skill):
    assert hard_filter_candidate(candidate(duty_codes=frozenset({duty})), required_duty=duty, required_skill=skill) == FilterReason.SKILL_MISMATCH


def test_unknown_is_not_relabelled_maintenance_and_low_confidence_is_preserved():
    with pytest.raises(ValueError, match='category is not a supported value'):
        mapped('not-a-category')
    assert mapped('other', 'other').low_confidence
    assert mapped('other', confidence=.4).low_confidence


def test_incident_follow_reuses_same_reservation_but_does_not_bypass_membership():
    follow = candidate(current_load=3, incident_follow=True)
    assert hard_filter_candidate(follow, required_duty='MAINTENANCE', required_skill='PLUMBING') is None
    assert hard_filter_candidate(replace(follow, property_effective=False), required_duty='MAINTENANCE', required_skill='PLUMBING') == FilterReason.PROPERTY_NOT_EFFECTIVE


@pytest.mark.parametrize('risk', ['safety', 'gas'])
def test_high_risk_still_cannot_auto_assign(risk):
    service = AutoDispatchService(None, settings=Settings(_env_file=None, ai_auto_assign=True))
    assert service.maybe_assign(work_order=object(), mapped=mapped(risk=risk), request_id='unit') == 'HIGH_RISK'


def test_global_reservation_query_is_current_locking_read_without_property_partition():
    session = MagicMock()
    session.scalars.return_value.all.return_value = [1, 2]
    count = WorkOrderRepository(session).employee_global_occupancy(employee_id=17, tenant_id=4, lock=True)
    statement = session.scalars.call_args.args[0]
    sql = str(statement.compile(dialect=mysql.dialect()))
    assert count == 2 and 'FOR UPDATE' in sql
    assert 'tenant_id' in sql and 'ended_at IS NULL' in sql and 'status' in sql
    assert 'property_id' not in sql  # Other-property unaccepted tasks are reservations too.


def test_waiting_scan_is_bounded_latest_analysis_and_oldest_attempt_first():
    session = MagicMock()
    session.execute.return_value.all.return_value = [(41, 7)]
    result = AiRepository(session).list_waiting_dispatch(retry_before=datetime(2026, 9, 24, tzinfo=UTC), limit=900)
    statement = session.execute.call_args.args[0]
    compiled = statement.compile(dialect=mysql.dialect())
    assert result == ((41, 7),)
    assert 100 in compiled.params.values()
    sql = str(compiled)
    assert 'max(work_order_analyses.id)' in sql and 'max(work_order_logs.created_at)' in sql
    assert 'ORDER BY' in sql and 'tenant_id' in sql and 'property_id' in sql
    assert 'PENDING_ASSIGNMENT' in compiled.params.values()


def test_waiting_scan_uses_stored_analysis_without_provider_or_new_queue(monkeypatch):
    service = AnalysisService.__new__(AnalysisService)
    service._session = MagicMock()
    service._settings = Settings(_env_file=None, ai_auto_assign=True)
    service._clock = lambda: datetime(2026, 9, 24, tzinfo=UTC)
    service._ai = MagicMock()
    service._ai.list_waiting_dispatch.return_value = [(41, 7), (42, 8)]
    service._retry_auto_dispatch_from_existing = MagicMock(side_effect=['NO_ELIGIBLE', 'ASSIGNED'])
    service._call_providers = MagicMock()
    monkeypatch.setattr('app.service.analysis.context.begin_owned_transaction', lambda _: nullcontext())
    assert service.sweep_waiting_dispatch() == {'scanned': 2, 'assigned': 1, 'waiting': 1}
    service._call_providers.assert_not_called()
    assert service._retry_auto_dispatch_from_existing.call_args_list[0].kwargs['analysis_id'] == 7


def test_assignment_lock_rechecks_new_global_reservation_before_insert():
    service = AutoDispatchService(MagicMock(), settings=Settings(_env_file=None))
    order = WorkOrder(id=41, tenant_id=4, property_id=9, status='PENDING_ASSIGNMENT', incident_id=None)
    employee = SimpleNamespace(id=17, tenant_id=4, status='ACTIVE', max_capacity=3, user_id=71)
    service._employees.get_employee_for_update = MagicMock(return_value=employee)
    service._employees.get_daily_status_for_update = MagicMock(return_value=SimpleNamespace(on_duty='ON_DUTY', current_load=0))
    service._work_orders.list_assignments = MagicMock(return_value=())
    service._work_orders.get_property = MagicMock(return_value=SimpleNamespace(timezone='Asia/Shanghai'))
    service._work_orders.employee_global_occupancy = MagicMock(return_value=1)
    service._ai.get_effective_property = MagicMock(return_value=object())
    service._ai.get_attendance = MagicMock(return_value=SimpleNamespace(check_in_at=datetime(2026, 9, 24, tzinfo=UTC)))
    service._ai.list_employee_duties = MagicMock(return_value=frozenset({'MAINTENANCE'}))
    service._ai.list_employee_skills = MagicMock(return_value=frozenset())
    service._ai.get_user_status = MagicMock(return_value='ACTIVE')
    service._ai.list_employee_role_codes = MagicMock(return_value=frozenset({'STAFF'}))
    service._insert_auto_assignment = MagicMock()
    result = service._commit_auto_assignment(record=order, mapped=mapped(), winner=candidate(), score=1,
                                             reasons=(), filter_snapshot=(), request_id='new-reservation')
    assert result == 'NO_ELIGIBLE'
    service._employees.get_employee_for_update.assert_called_once_with(17)
    service._work_orders.employee_global_occupancy.assert_called_once_with(employee_id=17, tenant_id=4, lock=True)
    service._insert_auto_assignment.assert_not_called()


def test_wait_reason_is_persisted_without_assignment_or_terminal_status():
    service = AutoDispatchService(MagicMock(), settings=Settings(_env_file=None))
    service._work_orders.add_log = MagicMock()
    service._work_orders.has_dispatch_wait_attempt = MagicMock(return_value=False)
    order = WorkOrder(id=41, tenant_id=4, property_id=9, status='PENDING_ASSIGNMENT', process_reason='NONE')
    service._record_wait(order, ({'filter_reason': 'BUSY'},), 'unit-wait')
    log = service._work_orders.add_log.call_args.args[0]
    assert log.payload_json['reason_code'] == 'WAITING_IDLE'
    assert log.event_code == 'AUTO_DISPATCH_WAIT'
    assert order.status == log.to_status == 'PENDING_ASSIGNMENT'


def test_contact_fields_redacted_before_structured_or_exception_logging():
    synthetic = '+8613800000123'
    assert synthetic not in str(redact_value({'contact_phone_e164': synthetic}))
    assert synthetic not in redact_text('contact_phone_e164="' + synthetic + '"')
    assert synthetic not in redact_text("{'phone': '" + synthetic + "'}")


def test_unavailable_scan_uses_current_property_date_not_yesterday_open_attendance():
    from datetime import date
    session = MagicMock()
    session.execute.return_value.all.return_value = [(9, 'Asia/Shanghai'), (10, 'America/Los_Angeles')]
    session.scalars.return_value.all.return_value = [41]
    now = datetime(2026, 9, 24, 17, 0, tzinfo=UTC)
    assert WorkOrderRepository(session).list_unavailable_unaccepted(now=now, limit=1000) == (41,)
    statement = session.scalars.call_args.args[0]
    compiled = statement.compile(dialect=mysql.dialect())
    assert date(2026, 9, 25) in compiled.params.values()
    assert date(2026, 9, 24) in compiled.params.values()
    assert 'max(' not in str(compiled).lower()
    assert 'business_date = CASE' in str(compiled)
    assert 100 in compiled.params.values()


def test_availability_commit_precedes_recovery_and_rollback_never_publishes(monkeypatch):
    from app.service.employee import EmployeeService
    service = EmployeeService.__new__(EmployeeService)
    events = []
    service._execute = lambda operation: (events.append('committed'), operation())[1]
    monkeypatch.setattr('app.worker.enqueue.schedule_dispatch_recovery', lambda _: events.append('publish'))
    assert service._execute_availability(lambda: 17) == 17
    assert events == ['committed', 'publish']
    events.clear()
    service._execute = MagicMock(side_effect=RuntimeError('rollback'))
    with pytest.raises(RuntimeError):
        service._execute_availability(lambda: 17)
    assert not events


def test_wait_attempt_duplicate_is_a_noop():
    service = AutoDispatchService(MagicMock(), settings=Settings(_env_file=None))
    service._work_orders.has_dispatch_wait_attempt = MagicMock(return_value=True)
    service._work_orders.add_log = MagicMock()
    service._record_wait(WorkOrder(id=41), (), 'duplicate')
    service._work_orders.add_log.assert_not_called()
