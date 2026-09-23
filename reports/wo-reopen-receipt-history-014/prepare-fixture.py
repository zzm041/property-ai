"""Additive AUTH001-only 014 fixtures, then normal authenticated business APIs.

Only organization/RBAC bootstrap uses the previously authorized Development
fixture boundary. Binding approval and employee invitation/claim are real APIs.
No historical IDs are reused as evidence and no business status is set via SQL.
"""
from pathlib import Path
import json
import runpy
import secrets

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.database import create_database_engine, run_owned_transaction
from app.domain.dev_seed import SEED_PERMISSIONS, SEED_ROLE_PERMISSIONS
from app.domain.iam import OrganizationScope, ScopeType
from app.model.identity import Permission, Role, RolePermission, UserRole
from app.service.property import PropertyService
from app.service.rbac_mutation import RbacMutationService

REPO = Path(__file__).resolve().parents[2]
OUT = Path(__file__).parent
PRIVATE = REPO / "tmp" / "wo-reopen-receipt-history-014" / "context.json"
helpers = runpy.run_path(str(REPO / "reports/notification-events-inbox-013/run-runtime.py"))


def ensure_rbac(session):
    def persist():
        permission_names = {item.code: item.name for item in SEED_PERMISSIONS}
        for role_code in ("RESIDENT", "STAFF", "TENANT_ADMIN"):
            role = session.scalar(select(Role).where(Role.code == role_code))
            if role is None:
                role = Role(code=role_code, name=role_code)
                session.add(role)
                session.flush()
            for code in SEED_ROLE_PERMISSIONS[role_code]:
                permission = session.scalar(select(Permission).where(Permission.code == code))
                if permission is None:
                    permission = Permission(code=code, name=permission_names[code])
                    session.add(permission)
                    session.flush()
                existing = session.scalar(select(RolePermission).where(
                    RolePermission.role_id == role.id, RolePermission.permission_id == permission.id,
                ))
                if existing is None:
                    session.add(RolePermission(role_id=role.id, permission_id=permission.id))
    run_owned_transaction(session, persist)


def main():
    settings = helpers["_guard"]()
    engine = create_database_engine(settings.test_database_url)
    with engine.connect() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "20260919_0009"
    PRIVATE.parent.mkdir(parents=True, exist_ok=True)
    ctx = json.loads(PRIVATE.read_text(encoding="utf-8")) if PRIVATE.exists() else {
        "run_id": f"wo014-{secrets.token_hex(6)}", "done": {}, "identities": {}, "phones": {},
    }
    run_id = ctx["run_id"]
    client = helpers["Client"](run_id)

    def save():
        PRIVATE.write_text(json.dumps(ctx, ensure_ascii=False, indent=2), encoding="utf-8")

    def step(label, operation):
        if label not in ctx["done"]:
            ctx["done"][label] = operation()
            save()
        return ctx["done"][label]

    def grant_manager(session, tenant_id):
        # A prior attempt may have committed before saving its checkpoint.
        # Inspect that exact grant instead of repeating a successful mutation.
        existing = session.scalar(select(UserRole.id).join(Role).where(
            UserRole.user_id == ctx["identities"]["manager"], Role.code == "TENANT_ADMIN",
            UserRole.scope_type == "TENANT", UserRole.tenant_id == tenant_id,
            UserRole.property_id.is_(None),
        ))
        session.rollback()
        if existing is not None:
            return {"existing_grant_id": existing, "reused_after_readonly_check": True}
        run_owned_transaction(session, lambda:
            RbacMutationService(session).grant_user_role_for_development_seed(
                target_user_id=ctx["identities"]["manager"], role_code="TENANT_ADMIN",
                scope=OrganizationScope(ScopeType.TENANT, tenant_id=tenant_id),
                request_id=f"{run_id}-bootstrap-manager",
            ))
        return {"created": True}

    def api(method, path, *, actor=None, body=None, label):
        return step(label, lambda: client.data(
            method, path, token=None if actor is None else ctx["done"][f"login-{actor}"]["access_token"],
            body=body, label=label,
        ))

    save()
    try:
        with Session(engine, autoflush=False, expire_on_commit=False) as session:
            service = PropertyService(session)
            tenant_id = step("tenant", lambda: service.create_tenant(name=f"014合成租户-{run_id}").id)
            property_id = step("property", lambda: service.create_property(
                tenant_id=tenant_id, name=f"014合成小区-{run_id}", address="仅用于隔离测试",
            ).id)
            building_id = step("building", lambda: service.create_building(
                tenant_id=tenant_id, property_id=property_id, code="WO014", name="014合成楼栋",
            ).id)
            unit_id = step("unit", lambda: service.create_unit(
                tenant_id=tenant_id, property_id=property_id, building_id=building_id, code="U014", name="014合成单元",
            ).id)
            house_id = step("house", lambda: service.create_house(
                tenant_id=tenant_id, property_id=property_id, unit_id=unit_id, room_no="014-01", floor=1,
            ).id)
            step("rbac-catalog", lambda: ensure_rbac(session))
            for identity in ("resident", "other", "manager", "staff", "backup"):
                if identity not in ctx["phones"]:
                    ctx["phones"][identity] = f"192{secrets.randbelow(100_000_000):08d}"
                    save()
                tokens = step(f"login-{identity}", lambda: client.sms_login(ctx["phones"][identity], f"login-{identity}"))
                ctx["identities"][identity] = tokens["user_id"]
                api("PATCH", "/me/profile", actor=identity,
                    body={"display_name": f"014-{identity}-{run_id[-6:]}"}, label=f"profile-{identity}")
            step("grant-manager-fixture", lambda: grant_manager(session, tenant_id))
        ctx.update(tenant_id=tenant_id, property_id=property_id, house_id=house_id)
        save()
        for identity in ("resident", "other"):
            binding = api("POST", "/resident-bind-requests", actor=identity, label=f"bind-{identity}", body={
                "house_id": house_id, "relation_type": "OWNER", "tenant_id": tenant_id, "property_id": property_id,
            })
            api("POST", f"/admin/resident-bind-requests/{binding['id']}/approve", actor="manager", label=f"approve-{identity}")
        duty = api("POST", "/admin/duties", actor="manager", label="duty", body={
            "tenant_id": tenant_id, "property_id": property_id, "code": "MAINTENANCE", "name": "014维修职责",
        })
        skill = api("POST", "/admin/skills", actor="manager", label="skill", body={
            "tenant_id": tenant_id, "property_id": property_id, "code": "ELECTRICAL", "name": "014电气技能",
        })
        ctx["employees"] = {}
        for identity in ("staff", "backup"):
            employee = api("POST", "/admin/employees", actor="manager", label=f"employee-{identity}", body={
                "tenant_id": tenant_id, "property_id": property_id,
                "employee_no": f"{run_id}-{identity}", "duty_ids": [duty["id"]], "skill_ids": [skill["id"]], "max_capacity": 8,
            })
            ctx["employees"][identity] = employee["id"]
            invite = api("POST", f"/admin/employees/{employee['id']}/invites", actor="manager", label=f"invite-{identity}", body={"phone": ctx["phones"][identity]})
            api("POST", "/staff/employee-claim", actor=identity, label=f"claim-{identity}", body={"token": invite["invite_token"]})
            api("POST", "/staff/attendance/checkin", actor=identity, label=f"checkin-{identity}", body={"tenant_id": tenant_id, "property_id": property_id})
        principals = {}
        for identity in ctx["identities"]:
            me = client.data("GET", "/me", token=ctx["done"][f"login-{identity}"]["access_token"], label=f"verify-{identity}")
            assert "SYSTEM_ADMIN" not in me["roles"]
            principals[identity] = {key: me[key] for key in ("user_id", "roles", "scopes")}
        save()
        evidence = {
            "task_id": "WO-REOPEN-RECEIPT-HISTORY-014", "run_id": run_id,
            "database": "127.0.0.1:3307/property_ai_auth001_test", "revision": "20260919_0009",
            "tenant_id": tenant_id, "property_id": property_id, "house_id": house_id,
            "identities": principals, "employees": ctx["employees"],
            "setup_mode": "Development organization and manager RBAC fixture; official SMS API authentication; normal binding/approval/invite/claim/checkin APIs. Not UI evidence.",
            "requests": client.requests,
        }
        (OUT / "fixture-evidence.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"run_id": run_id, "status": "FIXTURE_READY", "tenant_id": tenant_id, "property_id": property_id, "house_id": house_id}))
    finally:
        client.close()
        engine.dispose()


if __name__ == "__main__":
    main()
