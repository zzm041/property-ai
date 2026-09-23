"""017 manager cancellation: existing FSM, scope, assignment/load and queue rules."""
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.dependencies.auth import get_current_principal
from app.api.v1.supervisor_work_orders import router, get_work_order_service
from app.core.errors import AppError
from app.domain.iam import Principal, RoleGrant, OrganizationScope, ScopeType
from app.domain.work_order import WorkOrderStatus
from app.service.work_order import WorkOrderService

NOW = datetime(2026, 9, 23, 4, tzinfo=UTC)


def actor(kind='manager', *, tenant=1, property_id=2, user_id=9):
    scope = OrganizationScope(ScopeType.PROPERTY, tenant_id=tenant, property_id=property_id)
    permissions = {'work_order:read', 'work_order:cancel'}
    if kind == 'manager':
        permissions |= {'work_order:dispatch', 'work_order:decide'}
    return Principal(user_id=user_id, status='ACTIVE', display_name='017 synthetic',
                     grants=(RoleGrant(kind, scope, frozenset(permissions)),))


def setup(monkeypatch, status='ASSIGNED', *, who=None, shared=False):
    principal = who or actor()
    record = SimpleNamespace(id=41, tenant_id=1, property_id=2, reporter_user_id=7,
                             version=3, status=status, process_reason='NONE', current_assignment_id=51, incident_id=None)
    assignment = SimpleNamespace(id=51, employee_id=11, status='ACTIVE', ended_at=None, reason='AUTO_DISPATCH')
    daily = SimpleNamespace(current_load=1)
    service = WorkOrderService.__new__(WorkOrderService)
    service._reload_actor = MagicMock(return_value=principal)
    service._lock_work_order = MagicMock(return_value=record)
    service._repository = MagicMock()
    service._repository.get_active_assignment_for_update.return_value = assignment
    service._repository.get_active_assignee_user_id.return_value = 8
    service._employees = MagicMock()
    service._employees.get_employee.return_value = SimpleNamespace(id=11)
    service._clock = lambda: NOW
    service._session = MagicMock()
    service._write_log = MagicMock()
    service._append_audit = MagicMock()
    service._notify_users = MagicMock()
    service._adjust_load = MagicMock(side_effect=lambda *args, delta: setattr(daily, 'current_load', daily.current_load + delta))
    capacity = MagicMock()
    capacity.should_release_load.return_value = not shared
    monkeypatch.setattr('app.service.incident.IncidentCapacityService', MagicMock(return_value=capacity))
    return service, principal, record, assignment, daily, capacity


def cancel(service, principal, version=3):
    return service._cancel(actor=principal, work_order_id=41, request_id='cancel017-unit', reason=' duplicate report ', version=version)


@pytest.mark.parametrize('status', ['PENDING_ANALYSIS', 'PENDING_ASSIGNMENT', 'ASSIGNED', 'ACCEPTED'])
def test_legal_cancel_ends_assignment_once_retains_provenance_and_notifies(monkeypatch, status):
    s, p, row, assignment, daily, capacity = setup(monkeypatch, status)
    if status.startswith('PENDING'):
        s._repository.get_active_assignment_for_update.return_value = None
        row.current_assignment_id = None
    cancel(s, p)
    assert row.status == 'CANCELLED' and row.version == 4 and row.current_assignment_id is None
    assert s._write_log.call_args.kwargs['reason_detail'] == 'duplicate report'
    s._append_audit.assert_called_once()
    assert s._notify_users.call_args.kwargs['user_ids'] == (7, 8)
    assert s._repository.get_sla_clock.return_value.state == 'STOPPED'
    if not status.startswith('PENDING'):
        assert assignment.status == 'ENDED' and assignment.ended_at == NOW
        assert assignment.reason == 'AUTO_DISPATCH' and daily.current_load == 0
        capacity.should_release_load.assert_called_once_with(work_order=row, assignment=assignment)
    with pytest.raises(AppError) as conflict:
        cancel(s, p)
    assert conflict.value.code == 'WO.CONFLICT'
    s._write_log.assert_called_once()
    s._append_audit.assert_called_once()


def test_incident_follower_cancel_retains_shared_capacity(monkeypatch):
    s, p, row, assignment, daily, _ = setup(monkeypatch, shared=True)
    row.incident_id = 81
    cancel(s, p)
    assert assignment.status == 'ENDED' and row.current_assignment_id is None
    assert daily.current_load == 1
    s._adjust_load.assert_not_called()


@pytest.mark.parametrize('status', ['PROCESSING', 'WAIT_CONFIRM', 'COMPLETED', 'CANCELLED', 'CLOSED'])
def test_illegal_cancel_does_not_end_assignment_or_audit(monkeypatch, status):
    s, p, row, assignment, daily, capacity = setup(monkeypatch, status)
    with pytest.raises(AppError) as invalid:
        cancel(s, p)
    assert invalid.value.code == 'WO.INVALID_TRANSITION'
    assert row.status == status and assignment.status == 'ACTIVE' and daily.current_load == 1
    capacity.should_release_load.assert_not_called()
    s._write_log.assert_not_called()
    s._append_audit.assert_not_called()


@pytest.mark.parametrize('who', [actor('resident'), actor('staff-resident'), actor(tenant=8), actor(property_id=99)])
def test_only_scope_matching_manager_can_cancel_another_report(monkeypatch, who):
    s, p, row, assignment, daily, _ = setup(monkeypatch, who=who)
    # Even read ownership as the assigned employee must not grant cancellation.
    s._repository.get_active_assignment.return_value = assignment
    s._employees.get_employee_for_user.return_value = SimpleNamespace(id=assignment.employee_id)
    with pytest.raises(AppError) as denied:
        cancel(s, p)
    assert denied.value.status_code == 403
    assert row.status == 'ASSIGNED' and daily.current_load == 1
    s._write_log.assert_not_called()


def test_reporter_manager_existing_preassignment_policy_unchanged(monkeypatch):
    s, p, row, *_ = setup(monkeypatch, who=actor(user_id=7))
    with pytest.raises(AppError) as denied:
        cancel(s, p)
    assert denied.value.code == 'WO.INVALID_TRANSITION'
    assert row.status == 'ASSIGNED'


def queue_service():
    service = WorkOrderService.__new__(WorkOrderService)
    service._reload_actor = MagicMock(return_value=actor())
    service._repository = MagicMock()
    service._repository.get_property.return_value = SimpleNamespace(id=2, tenant_id=1)
    service._repository.list_queue_for_property.return_value = ()
    return service


def test_unstarted_queue_derives_exact_legal_fsm_states_before_limit():
    service = queue_service()
    service.list_supervisor_queue(actor=actor(), property_id=2, after_id=11, limit=7, view='unstarted')
    args = service._repository.list_queue_for_property.call_args.kwargs
    assert set(args.pop('statuses')) == {'PENDING_ANALYSIS', 'PENDING_ASSIGNMENT', 'ASSIGNED', 'ACCEPTED'}
    assert args == {'tenant_id': 1, 'property_id': 2, 'after_id': 11, 'limit': 7}
    service.list_supervisor_queue(actor=actor(), property_id=2)
    assert 'ASSIGNED' not in service._repository.list_queue_for_property.call_args.kwargs['statuses']


@pytest.mark.parametrize('query', ['view=bad', 'view=unstarted&limit=0', 'view=unstarted&after_id=-1'])
def test_queue_route_rejects_bad_query_before_business_service(query):
    app = FastAPI()
    app.include_router(router)
    service = MagicMock()
    app.dependency_overrides[get_current_principal] = lambda: actor()
    app.dependency_overrides[get_work_order_service] = lambda: service
    result = TestClient(app).get('/supervisor/work-orders?property_id=2&' + query)
    assert result.status_code == 422
    service.list_supervisor_queue.assert_not_called()


def test_queue_route_passes_selected_view_and_empty_final_cursor():
    app = FastAPI()
    app.include_router(router)
    service = MagicMock()
    service.list_supervisor_queue.return_value = ()
    app.dependency_overrides[get_current_principal] = lambda: actor()
    app.dependency_overrides[get_work_order_service] = lambda: service
    result = TestClient(app).get('/supervisor/work-orders?property_id=2&view=unstarted')
    assert result.status_code == 200 and result.json()['data']['next_cursor'] is None
    assert service.list_supervisor_queue.call_args.kwargs['view'] == 'unstarted'
