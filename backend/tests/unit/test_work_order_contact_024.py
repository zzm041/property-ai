"""Contact snapshot boundary tests. No live database or business requests."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError
from sqlalchemy.engine import make_url

from app.core.config import Settings
from app.core.errors import AppError
from app.core.logging import redact_text
from app.domain.identity import OrganizationScope, Principal, RoleGrant, ScopeType
from app.model.work_order import WorkOrder
from app.schema.work_order import CreateWorkOrderRequest, WorkOrderData
from app.service.work_order import WorkOrderService

ROOT = Path(__file__).resolve().parents[3]
PHONE = '+8613800000123'


def actor(user=7, *, tenant=4, property_id=9, management=False, system=False):
    scope = OrganizationScope(ScopeType.SYSTEM) if system else OrganizationScope(ScopeType.PROPERTY, tenant, property_id)
    permissions = {'work_order:read'} | ({'work_order:dispatch'} if management else set())
    return Principal(user, 'ACTIVE', 'synthetic', (RoleGrant('PROPERTY_ADMIN' if management else 'STAFF', scope, frozenset(permissions)),))


def service_for(principal, phone=PHONE):
    repository, employees, audit = MagicMock(), MagicMock(), MagicMock()
    order = WorkOrder(id=41, tenant_id=4, property_id=9, reporter_user_id=7, contact_phone_e164=phone)
    repository.get_work_order_for_update.return_value = order
    repository.get_active_assignment.return_value = None
    employees.get_employee_for_user.return_value = None
    service = WorkOrderService(MagicMock(), repository=repository, employee_repository=employees,
                               audit_service=audit, principal_loader=lambda _: principal,
                               settings=Settings(_env_file=None))
    service._execute = lambda operation, **_kwargs: operation()
    return service, order, repository, employees, audit


@pytest.mark.parametrize('phone', [None, '', '   ', '1234', 'garbage', '+0012345678', '+8613９９９９９９９９９', '+8613٩٩٩٩٩٩٩٩٩'])
def test_invalid_phone_rejected_by_schema_and_service(phone):
    with pytest.raises(ValidationError):
        CreateWorkOrderRequest(house_id=1, text='滴水', contact_phone=phone)
    service, *_ = service_for(actor())
    with pytest.raises(AppError) as error:
        service.create_work_order(actor=actor(), house_id=1, contact_phone=phone,
                                  request_id='unit-contact', idempotency_key='unit-contact', text='滴水')
    assert error.value.status_code == 422


def test_contact_missing_is_not_an_optional_create_contract():
    with pytest.raises(ValidationError):
        CreateWorkOrderRequest(house_id=1, text='滴水')
    assert 'contact_phone_e164' not in WorkOrderData.model_fields


@pytest.mark.parametrize('raw', ['13800000123', '+86 (138) 0000-0123', '008613800000123'])
def test_normalization_is_canonical_before_service_and_idempotency(raw):
    assert CreateWorkOrderRequest(house_id=1, contact_phone=raw).contact_phone == PHONE
    service, order, *_ = service_for(actor())
    service._create_work_order = MagicMock(return_value=(order, False))
    service.create_work_order(actor=actor(), house_id=1, contact_phone=raw,
                              request_id='unit-contact', idempotency_key='unit-contact', text='滴水')
    assert service._create_work_order.call_args.kwargs['contact_phone'] == PHONE
    arguments = dict(house_id=1, text='滴水', file_ids=(), category=None, priority=None, severity=None)
    assert service._request_hash(**arguments, contact_phone=PHONE) != service._request_hash(**arguments, contact_phone='+8613800000456')


@pytest.mark.parametrize('principal', [actor(), actor(8, management=True)])
def test_reporter_and_scoped_manager_reveal_snapshot_with_phone_free_audit(principal):
    service, order, _, _, audit = service_for(principal)
    masked = service.get_contact(actor=principal, work_order_id=41, reveal=False, request_id='contact-mask')
    assert masked['phone'] is None and PHONE not in str(masked)
    audit.append.assert_not_called()
    result = service.get_contact(actor=principal, work_order_id=41, reveal=True, request_id='contact-reveal')
    assert result['phone'] == PHONE == order.contact_phone_e164
    assert audit.append.call_args.kwargs['details'] == {'work_order_id': 41}
    assert PHONE not in str(audit.append.call_args)


@pytest.mark.parametrize('principal', [actor(8), actor(8, tenant=5, management=True), actor(8, property_id=10, management=True), actor(8, management=True, system=True)])
def test_other_resident_unrelated_staff_cross_scope_and_system_cannot_read_even_mask(principal):
    service, _, _, _, audit = service_for(principal)
    with pytest.raises(AppError) as error:
        service.get_contact(actor=principal, work_order_id=41, reveal=True, request_id='contact-denied')
    assert error.value.status_code == 403
    audit.append.assert_not_called()


def test_current_responsible_employee_only_and_ended_appointment_revokes_access():
    principal = actor(8)
    service, _, repository, employees, _ = service_for(principal)
    employees.get_employee_for_user.return_value = SimpleNamespace(id=17, tenant_id=4, status='ACTIVE')
    repository.get_active_assignment.return_value = SimpleNamespace(employee_id=17)
    employees.find_effective_assignment.return_value = object()
    assert service.get_contact(actor=principal, work_order_id=41, reveal=True, request_id='contact-staff')['phone'] == PHONE
    employees.find_effective_assignment.return_value = None
    with pytest.raises(AppError):
        service.get_contact(actor=principal, work_order_id=41, reveal=True, request_id='contact-ended')
    employees.find_effective_assignment.return_value = object()
    repository.get_active_assignment.return_value = SimpleNamespace(employee_id=18)
    with pytest.raises(AppError):
        service.get_contact(actor=principal, work_order_id=41, reveal=True, request_id='contact-previous')


def test_historical_null_is_not_filled_from_identity_or_other_residents():
    service, order, _, _, audit = service_for(actor(), phone=None)
    assert service.get_contact(actor=actor(), work_order_id=41, reveal=True, request_id='contact-historical') == {
        'collected': False, 'masked_phone': None, 'phone': None}
    assert order.contact_phone_e164 is None
    audit.append.assert_not_called()


@pytest.mark.parametrize('raw', ["{'contact_phone': '+86 (138) 0000-0123'}", '{"phone": "008613800000123"}', 'contact_phone_e164=+8613800000123'])
def test_accepted_phone_formats_do_not_leak_in_exception_text(raw):
    safe = redact_text(raw)
    assert '0123' not in safe and '[redacted]' in safe


def migration():
    spec = importlib.util.spec_from_file_location('contact_migration', ROOT / 'backend/alembic/versions/20260924_0011_contact_phone.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('host,port,database,environment', [
    ('127.0.0.1', 3307, 'property_ai_test', 'test'),
    ('127.0.0.1', 3307, 'property_ai', 'test'),
    ('127.0.0.1', 3306, 'property_ai_auth001_test', 'test'),
    ('localhost', 3307, 'property_ai_auth001_test', 'test'),
    ('127.0.0.1', 3307, 'property_ai_auth001_test', 'production'),
])
def test_migration_fails_closed_outside_exact_owner_approval(monkeypatch, host, port, database, environment):
    module = migration()
    operation = MagicMock()
    operation.get_bind.return_value.engine.url = make_url(f'mysql+pymysql://{host}:{port}/{database}')
    monkeypatch.setattr(module, 'op', operation)
    monkeypatch.setenv('APP_ENV', environment)
    with pytest.raises(RuntimeError):
        module.upgrade()
    operation.add_column.assert_not_called()


def test_migration_adds_only_nullable_no_default_column_and_never_backfills(monkeypatch):
    module = migration()
    operation = MagicMock()
    operation.get_bind.return_value.engine.url = make_url('mysql+pymysql://127.0.0.1:3307/property_ai_auth001_test')
    monkeypatch.setattr(module, 'op', operation)
    monkeypatch.setenv('APP_ENV', 'test')
    module.upgrade()
    assert module.down_revision == '20260919_0009'
    table, column = operation.add_column.call_args.args
    assert table == 'work_orders' and column.name == 'contact_phone_e164'
    assert column.nullable and column.type.length == 16 and column.server_default is None
    operation.execute.assert_not_called()
    with pytest.raises(RuntimeError):
        module.downgrade()
