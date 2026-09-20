"""Additive 0009 fixtures for the focused staff-management remediation evidence.

Uses normal services for a second property and a roster large enough to cross
the page cursor boundary. New identities are created through development SMS;
fixture grants are limited to the synthetic tenant/property. No existing row is
deleted, no business status is stamped directly, and no migration is run.
"""

from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
import re
import secrets
import subprocess
import sys

import httpx
from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"
OPS = REPO / "scripts" / "ops"
for candidate in (BACKEND, OPS):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from app.core.database import create_database_engine, run_owned_transaction  # noqa: E402
from app.domain.dev_seed import SEED_PERMISSIONS, SEED_ROLE_PERMISSIONS  # noqa: E402
from app.domain.iam import OrganizationScope, Principal, RoleGrant, ScopeType  # noqa: E402
from app.model.employee import Employee  # noqa: E402
from app.model.identity import Permission, Role, RolePermission, UserRole  # noqa: E402
from app.model.property import Property  # noqa: E402
from app.service.employee import EmployeeService  # noqa: E402
from app.service.property import PropertyService  # noqa: E402
from auth001_isolated_runtime import build_isolated_settings  # noqa: E402


API = "http://127.0.0.1:8083/api/v1"
OUT = Path(__file__).resolve().parent
PRIVATE_CONTEXT = REPO / "tmp" / "staff-mgmt-auto-dispatch-001" / "context.json"
PUBLIC_EVIDENCE = OUT / "remediation-prepare-evidence.json"
ROSTER_TARGET = 101


def _phone() -> str:
    return f"191{secrets.randbelow(100_000_000):08d}"


def _answer(prompt: str) -> str:
    match = re.fullmatch(r"(\d+)\s*\+\s*(\d+)\s*=\s*\?", prompt.strip())
    if match is None:
        raise RuntimeError("Development challenge format was not recognized")
    return str(int(match.group(1)) + int(match.group(2)))


def _post(client: httpx.Client, path: str, body: dict[str, object], request_id: str):
    response = client.post(path, json=body, headers={"X-Request-Id": request_id})
    if response.status_code != 200:
        code = response.json().get("error", {}).get("code", "UNKNOWN")
        raise RuntimeError(f"POST {path} returned {response.status_code} {code}")
    return response.json()["data"]


def _sms_login(client: httpx.Client, phone: str, prefix: str) -> dict[str, object]:
    challenge = _post(
        client,
        "/auth/challenge",
        {"phone": phone, "purpose": "login"},
        f"{prefix}-challenge",
    )
    _post(
        client,
        "/auth/sms/send",
        {
            "phone": phone,
            "purpose": "login",
            "challenge_id": challenge["challenge_id"],
            "challenge_answer": _answer(str(challenge["prompt"])),
        },
        f"{prefix}-send",
    )
    inbox = _post(
        client,
        "/auth/sms/test-inbox",
        {"phone": phone, "purpose": "login"},
        f"{prefix}-inbox",
    )
    return _post(
        client,
        "/auth/sms/login",
        {"phone": phone, "code": inbox["code"]},
        f"{prefix}-login",
    )


def _ensure_role(session: Session, code: str) -> Role:
    def persist() -> Role:
        names = {item.code: item.name for item in SEED_PERMISSIONS}
        role = session.scalar(select(Role).where(Role.code == code))
        if role is None:
            role = Role(code=code, name=code.replace("_", " ").title())
            session.add(role)
            session.flush()
        for permission_code in SEED_ROLE_PERMISSIONS[code]:
            permission = session.scalar(
                select(Permission).where(Permission.code == permission_code)
            )
            if permission is None:
                permission = Permission(
                    code=permission_code,
                    name=names[permission_code],
                )
                session.add(permission)
                session.flush()
            linked = session.scalar(
                select(RolePermission).where(
                    RolePermission.role_id == role.id,
                    RolePermission.permission_id == permission.id,
                )
            )
            if linked is None:
                session.add(
                    RolePermission(role_id=role.id, permission_id=permission.id)
                )
        session.flush()
        return role

    return run_owned_transaction(session, persist)


def _grant(
    session: Session,
    *,
    user_id: int,
    role: Role,
    tenant_id: int,
    property_id: int | None,
) -> None:
    scope_type = "PROPERTY" if property_id is not None else "TENANT"

    def persist() -> None:
        existing = session.scalar(
            select(UserRole).where(
                UserRole.user_id == user_id,
                UserRole.role_id == role.id,
                UserRole.scope_type == scope_type,
                UserRole.tenant_id == tenant_id,
                UserRole.property_id == property_id,
            )
        )
        if existing is None:
            session.add(
                UserRole(
                    user_id=user_id,
                    role_id=role.id,
                    scope_type=scope_type,
                    tenant_id=tenant_id,
                    property_id=property_id,
                )
            )
            session.flush()

    run_owned_transaction(session, persist)


def _original_manager_id(session: Session, tenant_id: int) -> int:
    role = session.scalar(select(Role).where(Role.code == "TENANT_ADMIN"))
    if role is None:
        raise RuntimeError("TENANT_ADMIN role missing")
    user_id = session.scalar(
        select(UserRole.user_id)
        .where(
            UserRole.role_id == role.id,
            UserRole.scope_type == "TENANT",
            UserRole.tenant_id == tenant_id,
        )
        .order_by(UserRole.id)
        .limit(1)
    )
    if user_id is None:
        raise RuntimeError("synthetic tenant admin missing")
    return int(user_id)


def main() -> int:
    context = json.loads(PRIVATE_CONTEXT.read_text(encoding="utf-8"))
    tenant_id = int(context["org"]["tenant_id"])
    property_id = int(context["org"]["property_id"])
    suffix = context["run_id"].rsplit("-", 1)[-1]
    settings = build_isolated_settings(enable_enqueue=True)
    engine = create_database_engine(settings.test_database_url)
    factory = sessionmaker(
        bind=engine,
        class_=Session,
        autoflush=False,
        expire_on_commit=False,
    )
    session = factory()
    client = httpx.Client(base_url=API, timeout=20.0)
    try:
        with engine.connect() as connection:
            revision = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
        if revision != "20260919_0009":
            raise RuntimeError(f"Unexpected isolated revision {revision}")

        remediation = context.get("remediation") or {}
        secondary_property_id = remediation.get("secondary_property_id")
        if secondary_property_id is None:
            secondary_name = f"人员扩展小区-{suffix}"
            existing_secondary = session.scalar(
                select(Property).where(
                    Property.tenant_id == tenant_id,
                    Property.name == secondary_name,
                )
            )
            session.rollback()
            if existing_secondary is None:
                existing_secondary = PropertyService(session).create_property(
                    tenant_id=tenant_id,
                    name=secondary_name,
                    address="合成测试扩展地址",
                )
            secondary_property_id = int(existing_secondary.id)

        manager_id = _original_manager_id(session, tenant_id)
        session.rollback()
        actor = Principal(
            user_id=manager_id,
            status="ACTIVE",
            display_name="synthetic tenant admin",
            grants=(
                RoleGrant(
                    role_code="TENANT_ADMIN",
                    scope=OrganizationScope(ScopeType.TENANT, tenant_id=tenant_id),
                    permissions=frozenset(SEED_ROLE_PERMISSIONS["TENANT_ADMIN"]),
                ),
            ),
            auth_method="fixture",
        )
        employee_service = EmployeeService(session)
        existing_employee_nos = set(
            session.scalars(
                select(Employee.employee_no).where(
                    Employee.tenant_id == tenant_id,
                    Employee.employee_no.like(f"PAGE-{suffix.upper()}-%"),
                )
            ).all()
        )
        session.rollback()
        created = 0
        for index in range(ROSTER_TARGET):
            employee_no = f"PAGE-{suffix.upper()}-{index + 1:03d}"
            if employee_no in existing_employee_nos:
                continue
            employee_service.create_employee(
                actor=actor,
                user_id=None,
                tenant_id=tenant_id,
                employee_no=employee_no,
                property_id=property_id,
                request_id=f"staff-roster-{suffix}-{index + 1:03d}",
                max_capacity=1,
            )
            created += 1

        tenant_role = _ensure_role(session, "TENANT_ADMIN")
        property_role = _ensure_role(session, "PROPERTY_ADMIN")
        remediation_manager_phone = remediation.get("manager_phone") or _phone()
        remediation_manager = _sms_login(
            client,
            remediation_manager_phone,
            f"staff-remediation-manager-{suffix}",
        )
        _grant(
            session,
            user_id=int(remediation_manager["user_id"]),
            role=tenant_role,
            tenant_id=tenant_id,
            property_id=None,
        )

        property_admin_phone = remediation.get("property_admin_phone") or _phone()
        property_admin = _sms_login(
            client,
            property_admin_phone,
            f"staff-property-admin-{suffix}",
        )
        _grant(
            session,
            user_id=int(property_admin["user_id"]),
            role=property_role,
            tenant_id=tenant_id,
            property_id=property_id,
        )
        headers = {"Authorization": f"Bearer {property_admin['access_token']}"}
        allowed = client.get(
            "/admin/employees",
            params={"tenant_id": tenant_id, "property_id": property_id, "limit": 1},
            headers=headers,
        )
        denied = client.get(
            "/admin/employees",
            params={
                "tenant_id": tenant_id,
                "property_id": secondary_property_id,
                "limit": 1,
            },
            headers=headers,
        )
        if allowed.status_code != 200 or denied.status_code != 403:
            raise RuntimeError(
                f"scope probe returned allowed={allowed.status_code} denied={denied.status_code}"
            )

        context["remediation"] = {
            "secondary_property_id": secondary_property_id,
            "manager_phone": remediation_manager_phone,
            "property_admin_phone": property_admin_phone,
        }
        PRIVATE_CONTEXT.write_text(
            json.dumps(context, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        roster_count = session.scalar(
            select(Employee.id)
            .where(Employee.tenant_id == tenant_id)
            .order_by(Employee.id.desc())
            .limit(1)
        )
        evidence = {
            "task_id": "STAFF-MGMT-AUTO-DISPATCH-001",
            "phase": "review_round_1_fixture_prepared",
            "captured_at": datetime.now(UTC).isoformat(),
            "head": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=REPO, text=True
            ).strip(),
            "environment": {
                "api": API,
                "database": "127.0.0.1:3307/property_ai_auth001_test",
                "revision": revision,
                "queue": "property_ai.auth001",
            },
            "scope": {
                "tenant_id": tenant_id,
                "primary_property_id": property_id,
                "secondary_property_id": secondary_property_id,
            },
            "roster": {
                "requested_fixture_count": ROSTER_TARGET,
                "created_this_run": created,
                "highest_employee_id": roster_count,
            },
            "scope_probe": {
                "property_admin_primary_status": allowed.status_code,
                "property_admin_secondary_status": denied.status_code,
            },
            "boundaries": {
                "additive_only": True,
                "normal_services_used_for_property_and_employees": True,
                "development_sms_used_for_identities": True,
                "fixture_grants_limited_to_synthetic_scope": True,
                "migration_run": False,
                "tables_deleted_or_cleared": False,
                "direct_business_status_write": False,
            },
            "redaction": "No phone, OTP, token, password, secret, or credential is emitted.",
        }
        PUBLIC_EVIDENCE.write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(json.dumps(evidence, ensure_ascii=False, indent=2))
        return 0
    finally:
        client.close()
        session.close()
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
