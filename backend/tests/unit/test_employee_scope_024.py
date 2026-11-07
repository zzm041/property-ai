"""Existing STAFF membership follows real claimed service appointments only."""
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace as NS
from unittest.mock import MagicMock

import pytest

from app.domain.iam import ScopeType
from app.service import employee_identity as mod

NOW = datetime(2026, 9, 24, tzinfo=UTC)


@pytest.fixture
def setup(monkeypatch):
    session, repo, mutation = MagicMock(), MagicMock(), MagicMock()
    monkeypatch.setattr(mod, 'require_owned_transaction', MagicMock())
    monkeypatch.setattr(mod, 'utc_now', lambda: NOW)
    monkeypatch.setattr(mod, 'RbacMutationRepository', lambda _: repo)
    monkeypatch.setattr('app.service.rbac_mutation.RbacMutationService', lambda _: mutation)
    repo.get_role_by_code.return_value = NS(id=7)
    repo.find_user_role_for_update.return_value = None
    employee = NS(id=3, tenant_id=2, user_id=19, status='ACTIVE')
    assignment = NS(employee_id=3, tenant_id=2, property_id=8, status='ACTIVE', valid_from=NOW, valid_to=None)
    return session, repo, mutation, employee, assignment


def grant(setup):
    session, _, _, employee, assignment = setup
    mod.grant_claimed_staff_property(session, employee=employee, assignment=assignment, actor_user_id=6, request_id='024-unit')


def test_claimed_appointment_adds_only_exact_staff_scope(setup):
    grant(setup)
    kwargs = setup[2]._grant_user_role_in_current_transaction.call_args.kwargs
    assert kwargs['target_user_id'] == 19 and kwargs['role_code'] == 'STAFF'
    assert (kwargs['scope'].scope_type, kwargs['scope'].tenant_id, kwargs['scope'].property_id) == (ScopeType.PROPERTY, 2, 8)


def test_existing_scope_is_idempotent_without_duplicate_grant_audit(setup):
    setup[1].find_user_role_for_update.return_value = NS(id=5)
    grant(setup)
    setup[2]._grant_user_role_in_current_transaction.assert_not_called()


def test_claim_appointment_current_read_cannot_reuse_pre_employee_lock_snapshot():
    from app.repository.employee import EmployeeRepository
    from sqlalchemy.dialects import mysql
    session = MagicMock()
    EmployeeRepository(session).list_assignments_for_employee(3, lock=True)
    statement = session.scalars.call_args.args[0]
    assert str(statement.compile(dialect=mysql.dialect())).endswith('FOR UPDATE')
    assert statement.get_execution_options()['populate_existing'] is True


def test_reenable_claimed_employee_repairs_effective_work_scopes(monkeypatch):
    from app.service.employee import EmployeeService
    service = EmployeeService.__new__(EmployeeService)
    service._session = MagicMock()
    service._repository = MagicMock()
    employee = NS(id=3, tenant_id=2, user_id=19, status='DISABLED')
    appointment = NS(id=44)
    service._repository.get_employee_for_update.return_value = employee
    service._repository.list_assignments_for_employee.return_value = (appointment,)
    service._reload_actor = MagicMock(return_value=NS(user_id=6))
    service._require_employee_permission = MagicMock()
    service._append_audit = MagicMock()
    grant = MagicMock()
    monkeypatch.setattr(mod, 'grant_claimed_staff_property', grant)
    service._update_employee(actor=NS(user_id=6), employee_id=3, request_id='reactivate',
                             status='ACTIVE', department_id=None, max_capacity=None,
                             add_duty_ids=None, add_skill_ids=None)
    assert employee.status == 'ACTIVE'
    grant.assert_called_once()
    service._repository.list_assignments_for_employee.assert_any_call(3, lock=True)


@pytest.mark.parametrize('which,key,value', [
    (3,'user_id',None),(3,'status','INACTIVE'),(4,'status','ENDED'),
    (4,'tenant_id',22),(4,'employee_id',99),(4,'valid_from',NOW+timedelta(days=1)),
    (4,'valid_to',NOW),
])
def test_invalid_or_unclaimed_relationship_never_grants(setup, which, key, value):
    setattr(setup[which], key, value)
    grant(setup)
    setup[2]._grant_user_role_in_current_transaction.assert_not_called()


@pytest.mark.parametrize('remaining,existing,expected', [(None,True,True),(1,True,False),(None,False,False)])
def test_end_revokes_only_staff_in_target_scope_if_no_remaining_assignment(setup, remaining, existing, expected):
    session, repo, mutation, employee, _ = setup
    session.scalar.return_value = remaining
    repo.find_user_role_for_update.return_value = NS(id=5) if existing else None
    mod.revoke_ended_staff_property(session, employee=employee, property_id=8, actor_user_id=6, request_id='024-end')
    call = mutation._revoke_user_role_in_current_transaction
    assert call.called is expected
    if expected:
        assert call.call_args.kwargs['role_code'] == 'STAFF'
        assert call.call_args.kwargs['scope'].property_id == 8
