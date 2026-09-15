"""Gate A identity and scoped-grant constraints on real MySQL 8."""

import pytest
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from app.model.identity import (
    AuditLog,
    Permission,
    Role,
    RolePermission,
    User,
    UserRole,
)
from app.service.property import PropertyService


def _create_user_and_role(session: Session) -> tuple[User, Role]:
    with session.begin():
        user = User(status="ACTIVE", display_name="Gate A User")
        role = Role(code="PROPERTY_ADMIN", name="Property Admin")
        session.add_all((user, role))
        session.flush()
    return user, role


def _create_tenant_property(
    session: Session,
    *,
    tenant_name: str,
    property_name: str,
) -> tuple[int, int]:
    service = PropertyService(session)
    tenant = service.create_tenant(name=tenant_name)
    property_record = service.create_property(
        tenant_id=tenant.id,
        name=property_name,
        address=f"{property_name} Address",
    )
    return tenant.id, property_record.id


def _persist_grant(
    session: Session,
    *,
    user_id: int,
    role_id: int,
    scope_type: str,
    tenant_id: int | None = None,
    property_id: int | None = None,
) -> UserRole:
    with session.begin():
        grant = UserRole(
            user_id=user_id,
            role_id=role_id,
            scope_type=scope_type,
            tenant_id=tenant_id,
            property_id=property_id,
        )
        session.add(grant)
        session.flush()
    return grant


def test_role_code_is_unique(identity_session: Session) -> None:
    with identity_session.begin():
        identity_session.add(Role(code="SYSTEM_ADMIN", name="System Admin"))

    with pytest.raises(IntegrityError):
        with identity_session.begin():
            identity_session.add(Role(code="SYSTEM_ADMIN", name="Duplicate"))
            identity_session.flush()


def test_permission_code_is_unique(identity_session: Session) -> None:
    with identity_session.begin():
        identity_session.add(Permission(code="role:read", name="Read roles"))

    with pytest.raises(IntegrityError):
        with identity_session.begin():
            identity_session.add(Permission(code="role:read", name="Duplicate"))
            identity_session.flush()


def test_role_permission_duplicate_is_rejected(identity_session: Session) -> None:
    with identity_session.begin():
        role = Role(code="TENANT_ADMIN", name="Tenant Admin")
        permission = Permission(code="role:manage", name="Manage roles")
        identity_session.add_all((role, permission))
        identity_session.flush()
        identity_session.add(
            RolePermission(role_id=role.id, permission_id=permission.id)
        )

    with pytest.raises(IntegrityError):
        with identity_session.begin():
            identity_session.add(
                RolePermission(role_id=role.id, permission_id=permission.id)
            )
            identity_session.flush()


@pytest.mark.parametrize(
    ("scope_type", "tenant_mode", "property_mode"),
    [
        ("SYSTEM", "set", "none"),
        ("TENANT", "none", "none"),
        ("TENANT", "set", "set"),
        ("PROPERTY", "set", "none"),
        ("SELF", "none", "none"),
    ],
)
def test_scope_shape_check_rejects_invalid_grants(
    identity_session: Session,
    scope_type: str,
    tenant_mode: str,
    property_mode: str,
) -> None:
    user, role = _create_user_and_role(identity_session)
    tenant_id, property_id = _create_tenant_property(
        identity_session,
        tenant_name="Tenant",
        property_name="Property",
    )
    selected_tenant = tenant_id if tenant_mode == "set" else None
    selected_property = property_id if property_mode == "set" else None

    with pytest.raises(DBAPIError) as captured:
        _persist_grant(
            identity_session,
            user_id=user.id,
            role_id=role.id,
            scope_type=scope_type,
            tenant_id=selected_tenant,
            property_id=selected_property,
        )
    assert captured.value.orig.args[0] == 3819


def test_property_scope_rejects_tenant_mismatch(identity_session: Session) -> None:
    user, role = _create_user_and_role(identity_session)
    _, first_property = _create_tenant_property(
        identity_session,
        tenant_name="Tenant 1",
        property_name="Property 1",
    )
    second_tenant, _ = _create_tenant_property(
        identity_session,
        tenant_name="Tenant 2",
        property_name="Property 2",
    )

    with pytest.raises(IntegrityError):
        _persist_grant(
            identity_session,
            user_id=user.id,
            role_id=role.id,
            scope_type="PROPERTY",
            tenant_id=second_tenant,
            property_id=first_property,
        )


@pytest.mark.parametrize("scope_type", ["SYSTEM", "TENANT", "PROPERTY"])
def test_duplicate_scope_grants_are_rejected(
    identity_session: Session,
    scope_type: str,
) -> None:
    user, role = _create_user_and_role(identity_session)
    tenant_id, property_id = _create_tenant_property(
        identity_session,
        tenant_name="Tenant",
        property_name="Property",
    )
    scope_values = {
        "SYSTEM": (None, None),
        "TENANT": (tenant_id, None),
        "PROPERTY": (tenant_id, property_id),
    }
    selected_tenant, selected_property = scope_values[scope_type]
    _persist_grant(
        identity_session,
        user_id=user.id,
        role_id=role.id,
        scope_type=scope_type,
        tenant_id=selected_tenant,
        property_id=selected_property,
    )

    with pytest.raises(IntegrityError):
        _persist_grant(
            identity_session,
            user_id=user.id,
            role_id=role.id,
            scope_type=scope_type,
            tenant_id=selected_tenant,
            property_id=selected_property,
        )


def test_generated_columns_are_computed_by_mysql(
    identity_session: Session,
) -> None:
    user, role = _create_user_and_role(identity_session)
    tenant_id, property_id = _create_tenant_property(
        identity_session,
        tenant_name="Tenant",
        property_name="Property",
    )
    for scope_type, selected_tenant, selected_property in (
        ("SYSTEM", None, None),
        ("TENANT", tenant_id, None),
        ("PROPERTY", tenant_id, property_id),
    ):
        _persist_grant(
            identity_session,
            user_id=user.id,
            role_id=role.id,
            scope_type=scope_type,
            tenant_id=selected_tenant,
            property_id=selected_property,
        )

    identity_session.expire_all()
    grants = {
        item.scope_type: item
        for item in identity_session.scalars(select(UserRole)).all()
    }

    assert grants["SYSTEM"].system_scope_marker == 1
    assert grants["SYSTEM"].tenant_scope_key is None
    assert grants["SYSTEM"].property_scope_key is None
    assert grants["TENANT"].system_scope_marker is None
    assert grants["TENANT"].tenant_scope_key == tenant_id
    assert grants["TENANT"].property_scope_key is None
    assert grants["PROPERTY"].system_scope_marker is None
    assert grants["PROPERTY"].tenant_scope_key is None
    assert grants["PROPERTY"].property_scope_key == property_id


def test_generated_columns_reject_application_supplied_values(
    identity_session: Session,
) -> None:
    user, role = _create_user_and_role(identity_session)

    with pytest.raises(DBAPIError) as captured:
        with identity_session.begin():
            identity_session.add(
                UserRole(
                    user_id=user.id,
                    role_id=role.id,
                    scope_type="SYSTEM",
                    tenant_id=None,
                    property_id=None,
                    system_scope_marker=1,
                )
            )
            identity_session.flush()

    assert captured.value.orig.args[0] == 3105


def test_distinct_tenant_and_property_grants_can_coexist(
    identity_session: Session,
) -> None:
    user, role = _create_user_and_role(identity_session)
    first_tenant, first_property = _create_tenant_property(
        identity_session,
        tenant_name="Tenant 1",
        property_name="Property 1",
    )
    second_tenant, second_property = _create_tenant_property(
        identity_session,
        tenant_name="Tenant 2",
        property_name="Property 2",
    )
    grants = (
        ("TENANT", first_tenant, None),
        ("TENANT", second_tenant, None),
        ("PROPERTY", first_tenant, first_property),
        ("PROPERTY", second_tenant, second_property),
    )

    for scope_type, tenant_id, property_id in grants:
        _persist_grant(
            identity_session,
            user_id=user.id,
            role_id=role.id,
            scope_type=scope_type,
            tenant_id=tenant_id,
            property_id=property_id,
        )

    assert identity_session.query(UserRole).count() == 4


def test_audit_log_accepts_valid_property_scope(
    identity_session: Session,
) -> None:
    user, _ = _create_user_and_role(identity_session)
    tenant_id, property_id = _create_tenant_property(
        identity_session,
        tenant_name="Tenant",
        property_name="Property",
    )

    with identity_session.begin():
        identity_session.add(
            AuditLog(
                actor_user_id=user.id,
                tenant_id=tenant_id,
                property_id=property_id,
                action="GATE_A.TEST",
                resource_type="UserRole",
                resource_id=None,
                request_id="gate-a-audit-test",
                details_json={"result": "accepted"},
            )
        )

    assert identity_session.query(AuditLog).count() == 1


def test_audit_log_rejects_property_tenant_mismatch(
    identity_session: Session,
) -> None:
    user, _ = _create_user_and_role(identity_session)
    _, first_property = _create_tenant_property(
        identity_session,
        tenant_name="Tenant 1",
        property_name="Property 1",
    )
    second_tenant, _ = _create_tenant_property(
        identity_session,
        tenant_name="Tenant 2",
        property_name="Property 2",
    )

    with pytest.raises(IntegrityError):
        with identity_session.begin():
            identity_session.add(
                AuditLog(
                    actor_user_id=user.id,
                    tenant_id=second_tenant,
                    property_id=first_property,
                    action="GATE_A.INVALID",
                    resource_type="UserRole",
                    resource_id=None,
                    request_id="gate-a-invalid-audit",
                    details_json=None,
                )
            )
            identity_session.flush()
