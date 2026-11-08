"""Development Seed idempotency, identity lookup, and concurrency tests on MySQL 8."""

from pathlib import Path

import pytest
from sqlalchemy import Engine, func, inspect, select, text
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import AppError, DevSeedErrorCode
from app.domain.dev_seed import (
    DEV_PROPERTY_NAME,
    DEV_SEED_LOCK_NAME,
    DEV_TENANT_NAME,
    SEED_PERMISSIONS,
    SEED_ROLE_PERMISSIONS,
    SEED_ROLES,
    SEED_USERS,
)
from app.model.employee import Employee
from app.model.identity import (
    AuditLog,
    Permission,
    Role,
    RolePermission,
    User,
    UserRole,
)
from app.model.property import Building, House, Property, Tenant, Unit
from app.model.resident import ResidentBindRequest, ResidentHouseRelation
from app.model.work_order import WorkOrder
from app.repository.dev_seed import DevSeedRepository
from app.service.dev_seed import run_development_seed

BACKEND_ROOT = Path(__file__).resolve().parents[2]
EXPECTED_ROLE_PERMISSIONS = sum(
    len(codes) for codes in SEED_ROLE_PERMISSIONS.values()
)
EXPECTED_AUDITS = (
    1
    + 1
    + len(SEED_ROLES)
    + len(SEED_PERMISSIONS)
    + len(SEED_USERS)
    + EXPECTED_ROLE_PERMISSIONS
    + len(SEED_USERS)
)


def _count(session: Session, model: type[object]) -> int:
    session.rollback()
    return int(session.scalar(select(func.count()).select_from(model)) or 0)


def _foundation_counts(session: Session) -> dict[str, int]:
    return {
        "tenants": _count(session, Tenant),
        "properties": _count(session, Property),
        "users": _count(session, User),
        "roles": _count(session, Role),
        "permissions": _count(session, Permission),
        "role_permissions": _count(session, RolePermission),
        "user_roles": _count(session, UserRole),
        "audit_logs": _count(session, AuditLog),
        "buildings": _count(session, Building),
        "units": _count(session, Unit),
        "houses": _count(session, House),
    }


def test_first_seed_creates_foundation_and_second_seed_is_idempotent(
    identity_session: Session,
    integration_settings: Settings,
) -> None:
    first = run_development_seed(
        integration_settings,
        request_id="c4-first-seed",
    )
    first_counts = _foundation_counts(identity_session)
    second = run_development_seed(
        integration_settings,
        request_id="c4-second-seed",
    )
    second_counts = _foundation_counts(identity_session)

    assert first.request_id == "c4-first-seed"
    assert second.request_id == "c4-second-seed"
    assert first_counts == second_counts
    assert first_counts["tenants"] == 1
    assert first_counts["properties"] == 1
    assert first_counts["users"] == len(SEED_USERS)
    assert first_counts["roles"] == len(SEED_ROLES)
    assert first_counts["permissions"] == len(SEED_PERMISSIONS)
    assert first_counts["role_permissions"] == EXPECTED_ROLE_PERMISSIONS
    assert first_counts["user_roles"] == len(SEED_USERS)
    assert first_counts["audit_logs"] == EXPECTED_AUDITS
    assert first_counts["buildings"] == 0
    assert first_counts["units"] == 0
    assert first_counts["houses"] == 0
    assert identity_session.scalar(
        select(func.count())
        .select_from(AuditLog)
        .where(AuditLog.request_id == "c4-second-seed")
    ) == 0


def test_seed_uses_business_keys_and_real_role_permission_ids(
    identity_session: Session,
    integration_settings: Settings,
    test_engine: Engine,
) -> None:
    with test_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO roles (code, name) "
                "VALUES ('ZZZ_GAP_ROLE', 'Gap'), ('YYY_GAP_ROLE', 'Gap')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO permissions (code, name) "
                "VALUES (:code1, 'Gap'), (:code2, 'Gap')"
            ),
            {"code1": "zzz:gap", "code2": "yyy:gap"},
        )
        connection.execute(
            text("DELETE FROM roles WHERE code IN ('ZZZ_GAP_ROLE', 'YYY_GAP_ROLE')")
        )
        connection.execute(
            text("DELETE FROM permissions WHERE code IN (:code1, :code2)"),
            {"code1": "zzz:gap", "code2": "yyy:gap"},
        )

    run_development_seed(integration_settings, request_id="c4-nonseq")
    identity_session.rollback()

    tenant = identity_session.scalar(
        select(Tenant).where(Tenant.name == DEV_TENANT_NAME)
    )
    property_record = identity_session.scalar(
        select(Property).where(
            Property.tenant_id == tenant.id,
            Property.name == DEV_PROPERTY_NAME,
        )
    )
    assert tenant is not None
    assert property_record is not None

    roles = {
        role.code: role
        for role in identity_session.scalars(select(Role)).all()
    }
    permissions = {
        permission.code: permission
        for permission in identity_session.scalars(select(Permission)).all()
    }
    assert set(roles) == {item.code for item in SEED_ROLES}
    assert set(permissions) == {item.code for item in SEED_PERMISSIONS}
    assert {role.id for role in roles.values()} != set(range(1, 7))
    assert {permission.id for permission in permissions.values()} != set(
        range(1, 5)
    )

    for role_code, permission_codes in SEED_ROLE_PERMISSIONS.items():
        for permission_code in permission_codes:
            mapping = identity_session.scalar(
                select(RolePermission).where(
                    RolePermission.role_id == roles[role_code].id,
                    RolePermission.permission_id == permissions[permission_code].id,
                )
            )
            assert mapping is not None

    users = {
        user.display_name: user
        for user in identity_session.scalars(select(User)).all()
    }
    expected_scopes = {
        "__DEV_SYSTEM_ADMIN__": ("SYSTEM_ADMIN", "SYSTEM", None, None),
        "__DEV_TENANT_ADMIN__": (
            "TENANT_ADMIN",
            "TENANT",
            tenant.id,
            None,
        ),
        "__DEV_PROPERTY_MANAGER__": (
            "PROPERTY_ADMIN",
            "PROPERTY",
            tenant.id,
            property_record.id,
        ),
        "__DEV_RESIDENT__": (
            "RESIDENT",
            "PROPERTY",
            tenant.id,
            property_record.id,
        ),
        "__DEV_STAFF__": (
            "STAFF",
            "PROPERTY",
            tenant.id,
            property_record.id,
        ),
    }
    for display_name, expected in expected_scopes.items():
        user = users[display_name]
        grant = identity_session.scalar(
            select(UserRole).where(UserRole.user_id == user.id)
        )
        role_code, scope_type, tenant_id, property_id = expected
        assert grant is not None
        assert grant.role_id == roles[role_code].id
        assert grant.scope_type == scope_type
        assert grant.tenant_id == tenant_id
        assert grant.property_id == property_id

    source = (BACKEND_ROOT / "app" / "service" / "dev_seed.py").read_text(
        encoding="utf-8"
    )
    assert "user_id=1" not in source
    assert "role_id=1" not in source
    assert "permission_id=1" not in source
    assert "tenant_id=1" not in source
    assert "property_id=1" not in source


def test_binary_exact_marker_does_not_match_case_variant(
    identity_session: Session,
    integration_settings: Settings,
) -> None:
    with identity_session.begin():
        identity_session.add(
            User(status="ACTIVE", display_name="__dev_system_admin__")
        )

    run_development_seed(integration_settings, request_id="c4-binary")
    identity_session.rollback()
    exact = identity_session.scalars(
        select(User).where(User.display_name.collate("utf8mb4_bin") == "__DEV_SYSTEM_ADMIN__")
    ).all()
    variant = identity_session.scalars(
        select(User).where(
            User.display_name.collate("utf8mb4_bin") == "__dev_system_admin__"
        )
    ).all()
    assert len(exact) == 1
    assert len(variant) == 1
    assert exact[0].id != variant[0].id


def test_duplicate_marker_fails_closed_without_writes(
    identity_session: Session,
    integration_settings: Settings,
) -> None:
    with identity_session.begin():
        identity_session.add(
            User(status="ACTIVE", display_name="__DEV_SYSTEM_ADMIN__")
        )
        identity_session.add(
            User(status="ACTIVE", display_name="__DEV_SYSTEM_ADMIN__")
        )

    with pytest.raises(AppError) as captured:
        run_development_seed(integration_settings, request_id="c4-duplicate")

    assert captured.value.code == DevSeedErrorCode.DATA_CONFLICT
    assert _foundation_counts(identity_session)["tenants"] == 0
    assert _foundation_counts(identity_session)["roles"] == 0
    assert _foundation_counts(identity_session)["audit_logs"] == 0


@pytest.mark.parametrize(
    "status",
    ["DISABLED", "MERGED"],
)
def test_conflicting_seed_user_fails_closed(
    identity_session: Session,
    integration_settings: Settings,
    status: str,
) -> None:
    with identity_session.begin():
        identity_session.add(
            User(status=status, display_name="__DEV_SYSTEM_ADMIN__")
        )

    with pytest.raises(AppError) as captured:
        run_development_seed(integration_settings, request_id="c4-disabled-user")

    assert captured.value.code == DevSeedErrorCode.DATA_CONFLICT
    assert _foundation_counts(identity_session)["tenants"] == 0
    assert _foundation_counts(identity_session)["audit_logs"] == 0


def test_existing_seed_user_with_wrong_role_fails_closed(
    identity_session: Session,
    integration_settings: Settings,
) -> None:
    with identity_session.begin():
        identity_session.add(
            User(status="ACTIVE", display_name="__DEV_SYSTEM_ADMIN__")
        )

    with pytest.raises(AppError) as captured:
        run_development_seed(integration_settings, request_id="c4-wrong-role")

    assert captured.value.code == DevSeedErrorCode.DATA_CONFLICT
    assert _foundation_counts(identity_session)["tenants"] == 0


def test_extra_role_permission_is_kept_and_missing_mappings_are_filled(
    identity_session: Session,
    integration_settings: Settings,
) -> None:
    with identity_session.begin():
        role = Role(code="STAFF", name="Staff")
        permission = Permission(code="property:read", name="Read property")
        identity_session.add_all([role, permission])
        identity_session.flush()
        identity_session.add(
            RolePermission(
                role_id=role.id,
                permission_id=permission.id,
            )
        )

    run_development_seed(integration_settings, request_id="c4-extra-mapping")
    identity_session.rollback()
    assert _count(identity_session, RolePermission) == EXPECTED_ROLE_PERMISSIONS + 1
    assert _count(identity_session, Role) == len(SEED_ROLES)
    staff = identity_session.scalar(select(Role).where(Role.code == "STAFF"))
    property_read = identity_session.scalar(
        select(Permission).where(Permission.code == "property:read")
    )
    extra = identity_session.scalar(
        select(RolePermission).where(
            RolePermission.role_id == staff.id,
            RolePermission.permission_id == property_read.id,
        )
    )
    assert extra is not None


def test_seed_does_not_write_task_4_rows_or_later_models(
    identity_session: Session,
    integration_settings: Settings,
    test_engine: Engine,
) -> None:
    run_development_seed(integration_settings, request_id="c4-no-task4")
    assert _count(identity_session, WorkOrder) == 0
    assert _count(identity_session, Employee) == 0
    assert _count(identity_session, Building) == 0
    assert _count(identity_session, House) == 0
    assert _count(identity_session, ResidentBindRequest) == 0
    assert _count(identity_session, ResidentHouseRelation) == 0
    source = (BACKEND_ROOT / "app" / "service" / "dev_seed.py").read_text(
        encoding="utf-8"
    )
    for forbidden in (
        "Building",
        "Unit",
        "House",
        "Employee",
        "ResidentHouseRelation",
        "Attendance",
        "WorkOrder",
    ):
        assert forbidden not in source


def test_lock_contention_times_out_without_writes(
    identity_session: Session,
    integration_settings: Settings,
    test_engine: Engine,
) -> None:
    settings = Settings(
        _env_file=None,
        app_env="test",
        allow_destructive_test_database=True,
        database_url=integration_settings.database_url,
        test_database_url=integration_settings.test_database_url,
        dev_seed_lock_timeout_seconds=0,
    )
    holder = test_engine.connect().execution_options(isolation_level="AUTOCOMMIT")
    try:
        acquired = holder.execute(
            text("SELECT GET_LOCK(:name, 0)"),
            {"name": DEV_SEED_LOCK_NAME},
        ).scalar()
        assert acquired == 1
        with pytest.raises(AppError) as captured:
            run_development_seed(settings, request_id="c4-lock-timeout")
        assert captured.value.code == DevSeedErrorCode.LOCK_UNAVAILABLE
        assert _foundation_counts(identity_session)["tenants"] == 0
        assert _foundation_counts(identity_session)["audit_logs"] == 0
    finally:
        holder.execute(
            text("SELECT RELEASE_LOCK(:name)"),
            {"name": DEV_SEED_LOCK_NAME},
        )
        holder.close()


def test_failure_releases_lock_and_rolls_back(
    identity_session: Session,
    integration_settings: Settings,
    monkeypatch,
) -> None:
    def fail_add_user(self, user: User) -> User:
        del self, user
        raise RuntimeError("forced mid-seed failure")

    monkeypatch.setattr(DevSeedRepository, "add_user", fail_add_user)
    with pytest.raises(AppError) as captured:
        run_development_seed(integration_settings, request_id="c4-mid-failure")
    assert captured.value.code == DevSeedErrorCode.FAILED
    assert _foundation_counts(identity_session)["tenants"] == 0
    assert _foundation_counts(identity_session)["users"] == 0
    assert _foundation_counts(identity_session)["roles"] == 0
    assert _foundation_counts(identity_session)["audit_logs"] == 0

    monkeypatch.undo()
    run_development_seed(integration_settings, request_id="c4-after-failure")
    assert _foundation_counts(identity_session)["users"] == len(SEED_USERS)
    assert _foundation_counts(identity_session)["audit_logs"] == EXPECTED_AUDITS


def test_every_actual_mutation_has_audit_and_noop_does_not_spam(
    identity_session: Session,
    integration_settings: Settings,
) -> None:
    run_development_seed(integration_settings, request_id="c4-audit-first")
    identity_session.rollback()
    actions = list(
        identity_session.scalars(
            select(AuditLog.action).where(AuditLog.request_id == "c4-audit-first")
        )
    )
    assert actions.count("DEV_SEED_TENANT_CREATED") == 1
    assert actions.count("DEV_SEED_PROPERTY_CREATED") == 1
    assert actions.count("DEV_SEED_ROLE_CREATED") == len(SEED_ROLES)
    assert actions.count("DEV_SEED_PERMISSION_CREATED") == len(SEED_PERMISSIONS)
    assert actions.count("DEV_SEED_USER_CREATED") == len(SEED_USERS)
    assert actions.count("IAM_ROLE_PERMISSION_GRANTED") == EXPECTED_ROLE_PERMISSIONS
    assert actions.count("IAM_USER_ROLE_GRANTED") == len(SEED_USERS)
    for audit in identity_session.scalars(
        select(AuditLog).where(AuditLog.request_id == "c4-audit-first")
    ):
        serialized = str(audit.details_json)
        assert "eyJ" not in serialized
        assert "Bearer" not in serialized
        assert audit.details_json is not None
        assert audit.details_json.get("source") == "DEVELOPMENT_SEED"

    run_development_seed(integration_settings, request_id="c4-audit-second")
    assert identity_session.scalar(
        select(func.count())
        .select_from(AuditLog)
        .where(AuditLog.request_id == "c4-audit-second")
    ) == 0
