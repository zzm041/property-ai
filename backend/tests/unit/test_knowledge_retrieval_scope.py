from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.core.errors import AppError, KnowledgeErrorCode
from app.domain.iam import OrganizationScope, Principal, RoleGrant, ScopeType
from app.domain.knowledge import KNOWLEDGE_READ
from app.service.retrieval import RetrievalService


def _grant(role: str, scope: OrganizationScope) -> RoleGrant:
    return RoleGrant(
        role_code=role,
        scope=scope,
        permissions=frozenset({KNOWLEDGE_READ}),
    )


def _service(property_record) -> RetrievalService:
    properties = MagicMock()
    properties.get_property_by_id.return_value = property_record
    residents = MagicMock()
    residents.list_active_relations_for_user.return_value = ()
    employees = MagicMock()
    employees.get_employee_for_user.return_value = None
    return RetrievalService(
        MagicMock(),
        repository=MagicMock(),
        property_repository=properties,
        resident_repository=residents,
        employee_repository=employees,
        settings=MagicMock(),
    )


def test_selected_property_uses_its_persisted_tenant_with_mixed_grants() -> None:
    actor = Principal(
        user_id=41,
        status="ACTIVE",
        display_name="mixed manager",
        grants=(
            _grant(
                "TENANT_ADMIN",
                OrganizationScope(ScopeType.TENANT, tenant_id=10),
            ),
            _grant(
                "PROPERTY_ADMIN",
                OrganizationScope(
                    ScopeType.PROPERTY,
                    tenant_id=20,
                    property_id=200,
                ),
            ),
        ),
    )
    service = _service(SimpleNamespace(id=200, tenant_id=20))

    acl = service._acl_for(
        actor=actor,
        property_id=200,
        claimed_tenant_id=None,
    )

    assert acl.tenant_id == 20
    assert acl.property_ids == frozenset({0, 200})
    assert acl.allow_all_properties is False


def test_system_grant_resolves_selected_property_tenant() -> None:
    actor = Principal(
        user_id=1,
        status="ACTIVE",
        display_name="system manager",
        grants=(
            _grant("SYSTEM_ADMIN", OrganizationScope(ScopeType.SYSTEM)),
        ),
    )
    service = _service(SimpleNamespace(id=300, tenant_id=30))

    acl = service._acl_for(
        actor=actor,
        property_id=300,
        claimed_tenant_id=None,
    )

    assert acl.tenant_id == 30
    assert acl.property_ids == frozenset({0, 300})


def test_claimed_tenant_must_match_selected_persisted_property() -> None:
    actor = Principal(
        user_id=1,
        status="ACTIVE",
        display_name="system manager",
        grants=(
            _grant("SYSTEM_ADMIN", OrganizationScope(ScopeType.SYSTEM)),
        ),
    )
    service = _service(SimpleNamespace(id=300, tenant_id=30))

    with pytest.raises(AppError) as exc_info:
        service._acl_for(
            actor=actor,
            property_id=300,
            claimed_tenant_id=31,
        )

    assert exc_info.value.code == KnowledgeErrorCode.SCOPE_MISMATCH
