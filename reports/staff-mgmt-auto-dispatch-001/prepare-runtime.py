"""Prepare an additive synthetic 0009 slice for staff-page dispatch evidence.

Creates a new tenant/property/house and one existing department through the
authorized development fixture boundary. Creates the tenant-admin identity
through the official development-SMS API, then grants only TENANT_ADMIN in the
new tenant. It never deletes rows, changes migrations, or stamps employee,
attendance, resident-relation, work-order, or assignment business state.
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
from app.model.employee import Department  # noqa: E402
from app.model.identity import Permission, Role, RolePermission, UserRole  # noqa: E402
from app.service.property import PropertyService  # noqa: E402
from auth001_isolated_runtime import build_isolated_settings  # noqa: E402


API = "http://127.0.0.1:8083/api/v1"
OUT = Path(__file__).resolve().parent
PRIVATE = REPO / "tmp" / "staff-mgmt-auto-dispatch-001"
PRIVATE_CONTEXT = PRIVATE / "context.json"
PUBLIC_EVIDENCE = OUT / "prepare-evidence.json"
ROLE_CODES = ("RESIDENT", "STAFF", "TENANT_ADMIN")


def _phone() -> str:
    return f"191{secrets.randbelow(100_000_000):08d}"


def _answer(prompt: str) -> str:
    match = re.fullmatch(r"(\d+)\s*\+\s*(\d+)\s*=\s*\?", prompt.strip())
    if match is None:
        raise RuntimeError("Development challenge format was not recognized")
    return str(int(match.group(1)) + int(match.group(2)))


def _post(client: httpx.Client, path: str, body: dict[str, object], request_id: str):
    response = client.post(
        path,
        json=body,
        headers={"X-Request-Id": request_id},
    )
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


def _ensure_rbac(session: Session) -> dict[str, Role]:
    def persist() -> dict[str, Role]:
        roles: dict[str, Role] = {}
        permission_names = {item.code: item.name for item in SEED_PERMISSIONS}
        permission_codes = {
            code
            for role_code in ROLE_CODES
            for code in SEED_ROLE_PERMISSIONS[role_code]
        }
        permissions: dict[str, Permission] = {}
        for code in permission_codes:
            permission = session.scalar(select(Permission).where(Permission.code == code))
            if permission is None:
                permission = Permission(code=code, name=permission_names[code])
                session.add(permission)
                session.flush()
            permissions[code] = permission
        for code in ROLE_CODES:
            role = session.scalar(select(Role).where(Role.code == code))
            if role is None:
                role = Role(code=code, name=code.replace("_", " ").title())
                session.add(role)
                session.flush()
            roles[code] = role
            for permission_code in SEED_ROLE_PERMISSIONS[code]:
                existing = session.scalar(
                    select(RolePermission).where(
                        RolePermission.role_id == role.id,
                        RolePermission.permission_id == permissions[permission_code].id,
                    )
                )
                if existing is None:
                    session.add(
                        RolePermission(
                            role_id=role.id,
                            permission_id=permissions[permission_code].id,
                        )
                    )
        session.flush()
        return roles

    return run_owned_transaction(session, persist)


def _grant_tenant_admin(
    session: Session,
    *,
    user_id: int,
    tenant_id: int,
) -> None:
    def persist() -> None:
        role = session.scalar(select(Role).where(Role.code == "TENANT_ADMIN"))
        if role is None:
            raise RuntimeError("TENANT_ADMIN role was not prepared")
        existing = session.scalar(
            select(UserRole).where(
                UserRole.user_id == user_id,
                UserRole.role_id == role.id,
                UserRole.scope_type == "TENANT",
                UserRole.tenant_id == tenant_id,
                UserRole.property_id.is_(None),
            )
        )
        if existing is None:
            session.add(
                UserRole(
                    user_id=user_id,
                    role_id=role.id,
                    scope_type="TENANT",
                    tenant_id=tenant_id,
                    property_id=None,
                )
            )
            session.flush()

    run_owned_transaction(session, persist)


def _create_department(
    session: Session,
    *,
    tenant_id: int,
    property_id: int,
    suffix: str,
) -> Department:
    def persist() -> Department:
        department = Department(
            tenant_id=tenant_id,
            property_id=property_id,
            code=f"OPS_{suffix.upper()}",
            name="本轮维修组",
            status="ACTIVE",
        )
        session.add(department)
        session.flush()
        return department

    return run_owned_transaction(session, persist)


def main() -> int:
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

        suffix = secrets.token_hex(4)
        property_service = PropertyService(session)
        tenant = property_service.create_tenant(name=f"staff-mgmt-{suffix}")
        property_record = property_service.create_property(
            tenant_id=tenant.id,
            name=f"人员闭环小区-{suffix}",
            address="合成测试地址",
        )
        building = property_service.create_building(
            tenant_id=tenant.id,
            property_id=property_record.id,
            code=f"B{suffix[:4]}",
            name="一号楼",
        )
        unit = property_service.create_unit(
            tenant_id=tenant.id,
            property_id=property_record.id,
            building_id=building.id,
            code=f"U{suffix[:4]}",
            name="一单元",
        )
        house = property_service.create_house(
            tenant_id=tenant.id,
            property_id=property_record.id,
            unit_id=unit.id,
            room_no="101",
            floor=1,
        )
        department = _create_department(
            session,
            tenant_id=tenant.id,
            property_id=property_record.id,
            suffix=suffix,
        )
        _ensure_rbac(session)

        phones = {
            "tenant_admin": _phone(),
            "resident": _phone(),
            "full": _phone(),
            "mismatch": _phone(),
            "ended": _phone(),
            "primary": _phone(),
        }
        manager = _sms_login(client, phones["tenant_admin"], f"staff-mgmt-{suffix}")
        _grant_tenant_admin(
            session,
            user_id=int(manager["user_id"]),
            tenant_id=tenant.id,
        )

        context = {
            "run_id": f"staff-mgmt-auto-dispatch-{suffix}",
            "phones": phones,
            "org": {
                "tenant_id": tenant.id,
                "property_id": property_record.id,
                "property_name": property_record.name,
                "building_id": building.id,
                "unit_id": unit.id,
                "house_id": house.id,
                "department_id": department.id,
            },
        }
        PRIVATE.mkdir(parents=True, exist_ok=True)
        PRIVATE_CONTEXT.write_text(
            json.dumps(context, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        evidence = {
            "task_id": "STAFF-MGMT-AUTO-DISPATCH-001",
            "phase": "fixture_prepared",
            "captured_at": datetime.now(UTC).isoformat(),
            "head": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=REPO, text=True
            ).strip(),
            "environment": {
                "api": API,
                "database": "127.0.0.1:3307/property_ai_auth001_test",
                "revision": revision,
                "queue": "property_ai.auth001",
                "llm_provider": "DEVELOPMENT",
            },
            "org": context["org"],
            "fixture_writes": [
                "new synthetic tenant/property/building/unit/house",
                "new property-scoped department",
                "new SMS-authenticated manager user",
                "TENANT_ADMIN fixture grant limited to new tenant",
            ],
            "business_state_left_for_pages": [
                "duty and skill catalog",
                "employees and invites",
                "invite claims",
                "attendance",
                "resident binding and approval",
                "work orders and automatic assignments",
            ],
            "boundaries": {
                "additive_only": True,
                "migration_run": False,
                "tables_deleted_or_cleared": False,
                "business_status_stamped_by_sql": False,
                "shared_database_touched": False,
                "real_model_verified": False,
            },
            "private_context": "tmp/staff-mgmt-auto-dispatch-001/context.json",
            "redaction": "No phone, token, password, secret, resident name, or credential is emitted.",
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
