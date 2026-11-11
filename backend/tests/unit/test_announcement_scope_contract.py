"""A manager role outside the target scope cannot enlarge resident audiences."""
from datetime import UTC, datetime
from types import SimpleNamespace as NS
from unittest.mock import MagicMock

import pytest

from app.domain.ai import ANNOUNCEMENT_READ
from app.domain.identity import OrganizationScope, Principal, RoleGrant, ScopeType
from app.service.announcement import AnnouncementService


def grant(role, tenant=7, property_id=9, permissions=(ANNOUNCEMENT_READ,)):
    scope = OrganizationScope(ScopeType.TENANT if property_id is None else ScopeType.PROPERTY,
                              tenant, property_id)
    return RoleGrant(role, scope, frozenset(permissions))


@pytest.fixture
def context():
    service = AnnouncementService.__new__(AnnouncementService)
    service._clock = lambda: datetime(2026, 11, 1, tzinfo=UTC)
    service._work_orders = MagicMock()
    service._work_orders.get_property.return_value = NS(id=9, tenant_id=7)
    service._residents = MagicMock()
    service._residents.list_active_relations_for_user.return_value = (
        (NS(relation_type='OWNER'), NS(tenant_id=7, property_id=9, unit_id=4)),)
    service._employees = MagicMock()
    service._employees.get_employee_for_user.return_value = None
    service._properties = MagicMock()
    service._properties.get_unit.return_value = NS(id=4, building_id=3)
    service._ai = MagicMock()
    # Model the actual repository audience predicate so the test checks content.
    service._ai.list_published_announcements.side_effect = lambda **kw: tuple(
        body for audience, body in [('RESIDENT', 'public notice'), ('STAFF', 'private staff notice')]
        if audience in kw['audiences'])
    return service


@pytest.mark.parametrize('manager', [
    grant('SUPERVISOR', 7, 10), grant('PROPERTY_ADMIN', 8, 20),
    grant('TENANT_ADMIN', 8, None), grant('SUPERVISOR', permissions=()),
])
def test_unrelated_or_permissionless_management_grant_cannot_reveal_staff_notice(context, manager):
    actor = Principal(5, 'ACTIVE', 'synthetic', (grant('RESIDENT'), manager))
    assert context.list_published(actor=actor, property_id=9) == ('public notice',)
    assert context._ai.list_published_announcements.call_args.kwargs['audiences'] == ('OWNER', 'RESIDENT')


@pytest.mark.parametrize('manager', [grant('SUPERVISOR'), grant('PROPERTY_ADMIN'), grant('TENANT_ADMIN', 7, None)])
def test_same_grant_target_manager_can_read_staff_notice(context, manager):
    actor = Principal(5, 'ACTIVE', 'synthetic', (manager,))
    assert context.list_published(actor=actor, property_id=9, audience='property') == ('public notice', 'private staff notice')


def test_plain_resident_audience_unchanged(context):
    actor = Principal(5, 'ACTIVE', 'synthetic', (grant('RESIDENT'),))
    assert context.list_published(actor=actor, property_id=9) == ('public notice',)
