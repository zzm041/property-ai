"""A source-bound diagnosis is read-only, scoped, and never a new AI call."""
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace as NS
from unittest.mock import MagicMock

import pytest

from app.core.errors import AppError
from app.domain.iam import Principal, RoleGrant, OrganizationScope, ScopeType
from app.domain.work_order import WORK_ORDER_READ, WORK_ORDER_DISPATCH
from app.service.work_order import WorkOrderService

NOW = datetime(2026, 11, 1, 12, tzinfo=UTC)


@pytest.fixture
def ctx():
    s = WorkOrderService.__new__(WorkOrderService)
    scope = OrganizationScope(ScopeType.PROPERTY, 7, 9)
    actor = Principal(5, 'ACTIVE', 'synthetic', (RoleGrant('SUPERVISOR', scope, frozenset({WORK_ORDER_READ, WORK_ORDER_DISPATCH})),))
    s._principal_loader = MagicMock(return_value=actor)
    s._clock = lambda: NOW
    s._repository = MagicMock()
    s._analysis = MagicMock()
    s._session = MagicMock()
    s._llm = MagicMock()
    record = NS(id=31, tenant_id=7, property_id=9, version=3, status='PENDING_ASSIGNMENT',
                process_reason='NONE', sla_due_at=NOW+timedelta(hours=1), updated_at=NOW)
    s._repository.get_work_order.return_value = record
    s._repository.get_current_process.return_value = None
    s._repository.latest_dispatch_wait.return_value = NS(id=71, tenant_id=7, property_id=9,
        work_order_id=31, created_at=NOW-timedelta(minutes=3), payload_json={'reason_code':'WAITING_IDLE', 'private':'sensitive raw text'})
    s._analysis.get_current_analysis.return_value = NS(id=61, status='COMPLETED')
    return NS(service=s, actor=actor, record=record, query=lambda: s.diagnose_work_order(
        actor=actor, work_order_id=31, property_id=9, version=3))


def test_wait_diagnosis_has_historical_source_and_no_raw_payload(ctx):
    result = ctx.query()
    assert result.work_order_id == 31 and result.version == 3
    wait = next(item for item in result.facts if item.code == 'WAITING_IDLE')
    assert wait.source_type == 'work_order_log' and wait.source_id == 71
    assert wait.recorded_at == NOW-timedelta(minutes=3)
    assert '最近一次' in wait.text
    assert 'sensitive raw text' not in result.model_dump_json()
    assert result.current_eligibility_checked is False
    ctx.service._session.commit.assert_not_called()
    ctx.service._repository.add_log.assert_not_called()
    ctx.service._llm.assert_not_called()


@pytest.mark.parametrize('failure', ['revoked', 'foreign_scope', 'property', 'version'])
def test_diagnosis_rejects_before_sensitive_reads(ctx, failure):
    if failure == 'revoked': ctx.service._principal_loader.return_value = Principal(5, 'ACTIVE', 'synthetic', ())
    if failure == 'foreign_scope':
        ctx.service._principal_loader.return_value = Principal(5, 'ACTIVE', 'synthetic', (RoleGrant('SUPERVISOR',
            OrganizationScope(ScopeType.PROPERTY, 7, 10), frozenset({WORK_ORDER_DISPATCH})),))
    if failure == 'property': ctx.record.property_id = 10
    if failure == 'version': ctx.record.version = 4
    with pytest.raises(AppError): ctx.query()
    ctx.service._repository.latest_dispatch_wait.assert_not_called()
    ctx.service._analysis.get_current_analysis.assert_not_called()


@pytest.mark.parametrize('status', ['COMPLETED', 'CLOSED', 'CANCELLED'])
def test_terminal_orders_do_not_report_active_sla_overdue(ctx, status):
    ctx.record.status = status
    ctx.record.sla_due_at = NOW-timedelta(hours=1)
    result = ctx.query()
    assert not any(item.code == 'SLA_OVERDUE' for item in result.facts)


def test_multiple_process_rounds_are_not_called_repeat_repair_count(ctx):
    ctx.record.status = 'PROCESSING'
    ctx.record.process_reason = 'WAITING_PARTS'
    ctx.record.sla_due_at = NOW-timedelta(hours=1)
    ctx.service._repository.get_current_process.return_value = NS(id=81, cycle_no=3)
    result = ctx.query()
    fact = next(item for item in result.facts if item.code == 'MULTIPLE_PROCESS_ROUNDS')
    assert fact.source_id == 81 and '不等同' in fact.text
    assert any(item.code == 'SLA_OVERDUE' for item in result.facts)
    assert any('配件' in action for action in result.suggested_actions)
    assert not any(item.code == 'WAITING_IDLE' for item in result.facts)


@pytest.mark.parametrize('payload', [None, {'reason_code':'secret arbitrary reason'}])
def test_unknown_wait_reason_is_not_echoed(ctx, payload):
    ctx.service._repository.latest_dispatch_wait.return_value.payload_json = payload
    result = ctx.query()
    assert 'secret arbitrary reason' not in result.model_dump_json()
    assert any('重新核实' in action for action in result.suggested_actions)


def test_missing_analysis_is_unknown_not_failure(ctx):
    ctx.record.status = 'PENDING_ANALYSIS'
    ctx.service._analysis.get_current_analysis.return_value = None
    ctx.service._repository.latest_dispatch_wait.return_value = None
    result = ctx.query()
    assert any(item.code == 'ANALYSIS_UNKNOWN' for item in result.facts)
    assert not any(item.code == 'ANALYSIS_FAILED' for item in result.facts)


@pytest.mark.parametrize('status', ['PENDING_ANALYSIS', 'PENDING_ASSIGNMENT', 'ASSIGNED', 'ACCEPTED',
                                  'PROCESSING', 'WAIT_CONFIRM', 'COMPLETED', 'CLOSED', 'CANCELLED'])
def test_all_current_states_have_bounded_guidance(ctx, status):
    ctx.record.status = status
    result = ctx.query()
    assert result.facts[0].code == 'CURRENT_STATE'
    assert '未识别' not in result.facts[0].text
    assert result.suggested_actions and result.current_eligibility_checked is False


def test_latest_wait_query_is_bounded_and_scope_filtered():
    from app.repository.work_order import WorkOrderRepository
    session = MagicMock()
    WorkOrderRepository(session).latest_dispatch_wait(work_order_id=31, tenant_id=7, property_id=9)
    statement = session.scalar.call_args.args[0]
    sql = str(statement)
    assert all(name in sql for name in ('work_order_logs.work_order_id =', 'work_order_logs.tenant_id =',
                                      'work_order_logs.property_id =', 'work_order_logs.event_code =', 'DESC', 'LIMIT'))
    assert 'FOR UPDATE' not in sql
    session.commit.assert_not_called()


def test_route_rejects_header_scope_mismatch_before_service():
    from starlette.requests import Request
    from app.api.v1.supervisor_work_orders import diagnose_work_order
    service = MagicMock()
    with pytest.raises(AppError) as error:
        diagnose_work_order(work_order_id=31, request=Request({'type':'http','headers':[]}), actor=MagicMock(),
                            service=service, property_id=9, version=3, x_property_id='10')
    assert error.value.status_code == 422
    service.diagnose_work_order.assert_not_called()
