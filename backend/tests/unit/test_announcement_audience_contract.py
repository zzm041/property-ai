"""Published notice views must not inherit the caller's other roles."""
from types import SimpleNamespace as NS
from unittest.mock import MagicMock

import pytest

from app.core.errors import AppError
from app.domain.identity import Principal
from test_announcement_scope_contract import context, grant


def actor(*grants, status='ACTIVE'):
    return Principal(5, status, 'synthetic', tuple(grants) or (grant('RESIDENT'),))


def test_dual_role_defaults_to_resident_content(context):
    assert context.list_published(actor=actor(grant('RESIDENT'), grant('SUPERVISOR')), property_id=9) == ('public notice',)


def test_dual_role_explicit_property_can_read_internal(context):
    assert context.list_published(actor=actor(grant('RESIDENT'), grant('SUPERVISOR')), property_id=9,
                                  audience='property') == ('public notice', 'private staff notice')


@pytest.mark.parametrize('kind', ['no_relation', 'no_permission', 'inactive'])
def test_resident_view_requires_current_relation_and_permission(context, kind):
    current = actor()
    if kind == 'no_relation': context._residents.list_active_relations_for_user.return_value = ()
    if kind == 'no_permission': current = actor(grant('RESIDENT', permissions=()))
    if kind == 'inactive': current = actor(status='DISABLED')
    with pytest.raises(AppError) as error:
        context.list_published(actor=current, property_id=9)
    assert error.value.status_code == 403
    context._ai.list_published_announcements.assert_not_called()


@pytest.mark.parametrize('manager', [grant('SUPERVISOR', 7, 10), grant('TENANT_ADMIN', 8, None),
                                    grant('SUPERVISOR', permissions=())])
def test_resident_cannot_request_internal_by_unrelated_role(context, manager):
    with pytest.raises(AppError) as error:
        context.list_published(actor=actor(grant('RESIDENT'), manager), property_id=9, audience='property')
    assert error.value.status_code == 403


@pytest.mark.parametrize('failure', [None, 'inactive', 'foreign_tenant', 'expired', 'no_role'])
def test_staff_internal_view_requires_live_scope(context, failure):
    context._residents.list_active_relations_for_user.return_value = ()
    context._employees.get_employee_for_user.return_value = NS(id=6, tenant_id=7, status='ACTIVE')
    context._employees.find_effective_assignment.return_value = object()
    current = actor(grant('STAFF'))
    if failure == 'inactive': context._employees.get_employee_for_user.return_value.status = 'DISABLED'
    if failure == 'foreign_tenant': context._employees.get_employee_for_user.return_value.tenant_id = 8
    if failure == 'expired': context._employees.find_effective_assignment.return_value = None
    if failure == 'no_role': current = actor(grant('RESIDENT'))
    if failure:
        with pytest.raises(AppError) as error:
            context.list_published(actor=current, property_id=9, audience='property')
        assert error.value.status_code == 403
    else:
        assert context.list_published(actor=current, property_id=9, audience='property') == ('private staff notice',)


def test_invalid_audience_is_rejected_before_repository_content(context):
    with pytest.raises(AppError) as error:
        context.list_published(actor=actor(), property_id=9, audience='everyone')
    assert error.value.status_code == 422
    context._ai.list_published_announcements.assert_not_called()


def test_route_forwards_explicit_audience_and_pagination():
    from starlette.requests import Request
    from app.api.v1.announcements import list_announcements
    service = MagicMock()
    service.list_published.return_value = ()
    request = Request({'type': 'http', 'headers': [], 'state': {'request_id': 'unit'}})
    current = actor(grant('SUPERVISOR'))
    list_announcements(request=request, actor=current, service=service, property_id=9,
                       after_id=5, limit=3, audience='property')
    service.list_published.assert_called_once_with(actor=current, property_id=9,
                                                 after_id=5, limit=3, audience='property')
