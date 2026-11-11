"""PA-196: the seed now carries a SUPERVISOR demo account (role_id=3).

t167 found `user_roles` had no SUPERVISOR grant at all, so
`POST /api/v1/supervisor/work-orders/{id}/analyze` answered IAM.FORBIDDEN for every
authenticated user: the manual-remedy entry point was empty. These tests pin the seed
definition, the permission the endpoint actually requires, and the idempotency of the
credential rows the seed maintains.

No database is needed: the seed service is assembled with an in-memory repository.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.core.errors import AppError, IamErrorCode
from app.core.passwords import require_new_password, verify_password
from app.domain.dev import (
    DEV_SUPERVISOR_PASSWORD,
    DEV_SUPERVISOR_PHONE,
    SEED_ROLE_PERMISSIONS,
    SEED_USERS,
)
from app.domain.dev.models import SeedUserDefinition
from app.domain.identity import OrganizationScope, Principal, RoleGrant, ScopeType
from app.service.dev_seed import DevSeedService
from app.service.identity.rbac import RbacService

ANALYSIS_REVIEW = "ai_analysis:review"


def _definition() -> SeedUserDefinition:
    matches = [item for item in SEED_USERS if item.role_code == "SUPERVISOR"]
    assert len(matches) == 1
    return matches[0]


def test_seed_carries_exactly_one_supervisor_with_a_demo_login() -> None:
    definition = _definition()

    assert definition.display_name == "__DEV_SUPERVISOR__"
    assert definition.scope_type is ScopeType.PROPERTY
    assert definition.phone_e164 == DEV_SUPERVISOR_PHONE
    assert definition.phone_e164.startswith("+86139"), definition.phone_e164
    assert len(definition.phone_e164) == 14
    assert definition.password == DEV_SUPERVISOR_PASSWORD
    require_new_password(definition.password)
    assert definition.scope_tenant_id == 5
    assert definition.scope_property_id == 5


def test_supervisor_role_carries_the_permission_the_analyze_endpoint_requires() -> None:
    assert ANALYSIS_REVIEW in SEED_ROLE_PERMISSIONS["SUPERVISOR"]


def test_supervisor_principal_passes_the_analyze_gate_a_staff_principal_does_not() -> None:
    scope = OrganizationScope(ScopeType.PROPERTY, tenant_id=5, property_id=5)
    supervisor = Principal(
        user_id=196,
        status="ACTIVE",
        display_name="__DEV_SUPERVISOR__",
        grants=(
            RoleGrant(
                role_code="SUPERVISOR",
                scope=scope,
                permissions=frozenset(SEED_ROLE_PERMISSIONS["SUPERVISOR"]),
            ),
        ),
    )
    staff = Principal(
        user_id=197,
        status="ACTIVE",
        display_name="__DEV_STAFF__",
        grants=(
            RoleGrant(
                role_code="STAFF",
                scope=scope,
                permissions=frozenset(SEED_ROLE_PERMISSIONS["STAFF"]),
            ),
        ),
    )

    RbacService.require_permission(supervisor, ANALYSIS_REVIEW, scope)
    with pytest.raises(AppError) as raised:
        RbacService.require_permission(staff, ANALYSIS_REVIEW, scope)
    assert raised.value.code == IamErrorCode.FORBIDDEN


def test_supervisor_grant_does_not_leak_into_another_property() -> None:
    granted = OrganizationScope(ScopeType.PROPERTY, tenant_id=5, property_id=5)
    other = OrganizationScope(ScopeType.PROPERTY, tenant_id=5, property_id=6)
    supervisor = Principal(
        user_id=196,
        status="ACTIVE",
        display_name="__DEV_SUPERVISOR__",
        grants=(
            RoleGrant(
                role_code="SUPERVISOR",
                scope=granted,
                permissions=frozenset(SEED_ROLE_PERMISSIONS["SUPERVISOR"]),
            ),
        ),
    )

    with pytest.raises(AppError) as raised:
        RbacService.require_permission(supervisor, ANALYSIS_REVIEW, other)
    assert raised.value.code == IamErrorCode.FORBIDDEN


class _RecordingRepository:
    """In-memory stand-in for the seed repository's credential and scope lookups."""

    def __init__(self) -> None:
        self.phones: list[SimpleNamespace] = []
        self.passwords: list[SimpleNamespace] = []
        self.tenants: dict[int, SimpleNamespace] = {}
        self.properties: dict[int, SimpleNamespace] = {}
        self.phone_owner: SimpleNamespace | None = None

    def find_phone_owner(self, phone_e164: str):
        if self.phone_owner is not None:
            return self.phone_owner
        return next(
            (row for row in self.phones if row.phone_e164 == phone_e164 and row.status == "ACTIVE"),
            None,
        )

    def get_active_phone(self, user_id: int):
        return next(
            (
                row
                for row in self.phones
                if row.user_id == user_id and row.status == "ACTIVE"
            ),
            None,
        )

    def get_password(self, user_id: int):
        return next((row for row in self.passwords if row.user_id == user_id), None)

    def add_phone(self, phone):
        phone.id = len(self.phones) + 1
        self.phones.append(phone)
        return phone

    def add_password(self, password):
        password.id = len(self.passwords) + 1
        self.passwords.append(password)
        return password

    def get_tenant(self, tenant_id: int):
        return self.tenants.get(tenant_id)

    def get_property(self, property_id: int):
        return self.properties.get(property_id)


def _service(repository: _RecordingRepository) -> DevSeedService:
    service = object.__new__(DevSeedService)
    service._session = SimpleNamespace(in_transaction=lambda: True)
    service._repository = repository
    return service


def test_seeding_twice_leaves_one_phone_and_one_password_row() -> None:
    repository = _RecordingRepository()
    service = _service(repository)
    definition = _definition()
    user = SimpleNamespace(id=196, status="ACTIVE")

    service._ensure_credentials(user, definition)
    service._ensure_credentials(user, definition)

    assert len(repository.phones) == 1
    assert len(repository.passwords) == 1
    assert repository.phones[0].phone_e164 == DEV_SUPERVISOR_PHONE
    assert repository.phones[0].verified_at is not None
    assert repository.passwords[0].password_algo == "argon2id"
    assert verify_password(repository.passwords[0].password_hash, DEV_SUPERVISOR_PASSWORD)


def test_a_definitions_without_credentials_stays_identity_only() -> None:
    repository = _RecordingRepository()
    service = _service(repository)
    definition = SeedUserDefinition("__DEV_STAFF__", "STAFF", ScopeType.PROPERTY)

    service._ensure_credentials(SimpleNamespace(id=197, status="ACTIVE"), definition)

    assert repository.phones == []
    assert repository.passwords == []


def test_a_phone_owned_by_another_user_is_a_conflict_not_a_rebind() -> None:
    repository = _RecordingRepository()
    repository.phone_owner = SimpleNamespace(id=1, user_id=999, phone_e164=DEV_SUPERVISOR_PHONE)
    service = _service(repository)

    with pytest.raises(AppError):
        service._ensure_credentials(SimpleNamespace(id=196, status="ACTIVE"), _definition())

    assert repository.phones == []
    assert repository.passwords == []


def test_a_different_stored_hash_is_a_conflict() -> None:
    repository = _RecordingRepository()
    repository.phones.append(
        SimpleNamespace(
            id=1,
            user_id=196,
            phone_e164=DEV_SUPERVISOR_PHONE,
            status="ACTIVE",
        )
    )
    repository.passwords.append(
        SimpleNamespace(
            id=1,
            user_id=196,
            password_hash="$argon2id$v=19$m=65536,t=3,p=4$c2FsdA$aGFzaA",
            password_algo="argon2id",
        )
    )
    service = _service(repository)

    with pytest.raises(AppError):
        service._ensure_credentials(SimpleNamespace(id=196, status="ACTIVE"), _definition())


def test_declared_scope_is_used_when_that_tenant_and_property_exist() -> None:
    repository = _RecordingRepository()
    repository.tenants[5] = SimpleNamespace(id=5, status="ACTIVE")
    repository.properties[5] = SimpleNamespace(id=5, tenant_id=5)
    service = _service(repository)
    seeded_tenant = SimpleNamespace(id=1)
    seeded_property = SimpleNamespace(id=1)

    scope = service._scope_for(_definition(), seeded_tenant, seeded_property)

    assert scope.scope_type is ScopeType.PROPERTY
    assert (scope.tenant_id, scope.property_id) == (5, 5)


def test_declared_scope_falls_back_to_the_seeded_property_when_absent() -> None:
    repository = _RecordingRepository()
    service = _service(repository)
    seeded_tenant = SimpleNamespace(id=1)
    seeded_property = SimpleNamespace(id=1, tenant_id=1)

    scope = service._scope_for(_definition(), seeded_tenant, seeded_property)

    assert scope.scope_type is ScopeType.PROPERTY
    assert (scope.tenant_id, scope.property_id) == (1, 1)


def test_declared_scope_is_ignored_when_property_belongs_to_another_tenant() -> None:
    repository = _RecordingRepository()
    repository.tenants[5] = SimpleNamespace(id=5, status="ACTIVE")
    repository.properties[5] = SimpleNamespace(id=5, tenant_id=7)
    service = _service(repository)

    scope = service._scope_for(_definition(), SimpleNamespace(id=1), SimpleNamespace(id=1))

    assert (scope.tenant_id, scope.property_id) == (1, 1)
