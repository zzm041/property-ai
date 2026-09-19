"""Prepare MP-CURRENT-FULL-REGRESSION-001 env and synthetic accounts.

测试前置准备 only: org/RBAC/grants/employee+invite via isolated DB + 8083 HTTP.
Does not stamp APPROVED/ACTIVE/CLOSED, does not Seed-login, does not grant SYSTEM.
Does not print phones, tokens, or passwords.
"""

from __future__ import annotations

import json
import re
import secrets
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx
from sqlalchemy import inspect, select, text
from sqlalchemy.orm import Session, sessionmaker

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"
sys.path.insert(0, str(BACKEND))

from app.core.config import Settings  # noqa: E402
from app.core.database import (  # noqa: E402
    ISOLATED_AUTH_DATABASE,
    ISOLATED_AUTH_HOST,
    ISOLATED_AUTH_PORT,
    create_database_engine,
)
from app.domain.dev_seed import SEED_PERMISSIONS, SEED_ROLE_PERMISSIONS  # noqa: E402
from app.model.identity import Permission, Role, RolePermission, UserRole  # noqa: E402
from app.service.property import PropertyService  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402

from app.core.database import run_owned_transaction  # noqa: E402

OUT = Path(__file__).resolve().parent
PRIVATE = OUT / "private"
ENV_LOCAL = REPO / "miniprogram" / "config" / "env.local.js"
API = "http://127.0.0.1:8083"
ROLE_CODES = ("RESIDENT", "TENANT_ADMIN", "PROPERTY_ADMIN", "STAFF", "SUPERVISOR")
PASSWORD = "goodpass1"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _answer(prompt: str) -> str:
    match = re.fullmatch(r"(\d+) \+ (\d+) = \?", prompt.strip())
    if match is None:
        raise RuntimeError("unexpected challenge prompt")
    return str(int(match.group(1)) + int(match.group(2)))


def _phone() -> str:
    return f"139{secrets.randbelow(100_000_000):08d}"


def _isolated_url() -> str:
    env_file = next(
        (path for path in (BACKEND / ".env", REPO / ".env") if path.exists()),
        None,
    )
    settings = Settings(_env_file=str(env_file) if env_file else None)
    parsed = make_url(settings.test_database_url).set(database=ISOLATED_AUTH_DATABASE)
    if parsed.host != ISOLATED_AUTH_HOST:
        raise RuntimeError("isolated host mismatch")
    if (parsed.port or 3306) != ISOLATED_AUTH_PORT:
        raise RuntimeError("isolated port mismatch")
    if parsed.database != ISOLATED_AUTH_DATABASE:
        raise RuntimeError("isolated database mismatch")
    return parsed.render_as_string(hide_password=False)


def _session_factory(url: str):
    engine = create_database_engine(url)
    factory = sessionmaker(
        bind=engine,
        class_=Session,
        autoflush=False,
        expire_on_commit=False,
    )
    return engine, factory


def _ensure_rbac(session: Session) -> dict[str, Role]:
    def _persist() -> dict[str, Role]:
        roles: dict[str, Role] = {}
        for code in ROLE_CODES:
            role = session.scalar(select(Role).where(Role.code == code))
            if role is None:
                role = Role(code=code, name=code.replace("_", " ").title())
                session.add(role)
                session.flush()
            roles[code] = role
        needed = {
            code
            for role_code in ROLE_CODES
            for code in SEED_ROLE_PERMISSIONS[role_code]
        }
        names = {item.code: item.name for item in SEED_PERMISSIONS}
        permissions: dict[str, Permission] = {}
        for code in needed:
            permission = session.scalar(select(Permission).where(Permission.code == code))
            if permission is None:
                permission = Permission(code=code, name=names.get(code, code))
                session.add(permission)
                session.flush()
            permissions[code] = permission
        for role_code in ROLE_CODES:
            for permission_code in SEED_ROLE_PERMISSIONS[role_code]:
                existing = session.scalar(
                    select(RolePermission).where(
                        RolePermission.role_id == roles[role_code].id,
                        RolePermission.permission_id == permissions[permission_code].id,
                    )
                )
                if existing is None:
                    session.add(
                        RolePermission(
                            role_id=roles[role_code].id,
                            permission_id=permissions[permission_code].id,
                        )
                    )
        session.flush()
        return roles

    return run_owned_transaction(session, _persist)


def _grant(
    session: Session,
    *,
    user_id: int,
    role_code: str,
    scope_type: str,
    tenant_id: int,
    property_id: int | None,
) -> None:
    def _persist() -> None:
        role = session.scalar(select(Role).where(Role.code == role_code))
        if role is None:
            raise RuntimeError(f"missing role {role_code}")
        existing = session.scalar(
            select(UserRole).where(
                UserRole.user_id == user_id,
                UserRole.role_id == role.id,
                UserRole.scope_type == scope_type,
                UserRole.tenant_id == tenant_id,
                UserRole.property_id == property_id
                if property_id is not None
                else UserRole.property_id.is_(None),
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

    run_owned_transaction(session, _persist)


def _bootstrap_org(session: Session) -> dict[str, object]:
    suffix = secrets.token_hex(3)
    properties = PropertyService(session)
    tenant = properties.create_tenant(name=f"cfull-tenant-{suffix}")
    prop = properties.create_property(
        tenant_id=tenant.id,
        name=f"cfull-property-{suffix}",
        address="CFull isolated street",
    )
    building = properties.create_building(
        tenant_id=tenant.id,
        property_id=prop.id,
        code=f"B{suffix[:4]}",
        name="Building 1",
    )
    unit = properties.create_unit(
        tenant_id=tenant.id,
        property_id=prop.id,
        building_id=building.id,
        code=f"U{suffix[:4]}",
        name="Unit 1",
    )
    house = properties.create_house(
        tenant_id=tenant.id,
        property_id=prop.id,
        unit_id=unit.id,
        room_no="101",
        floor=1,
    )
    _ensure_rbac(session)
    return {
        "suffix": suffix,
        "tenant_id": tenant.id,
        "property_id": prop.id,
        "property_name": prop.name,
        "building_id": building.id,
        "unit_id": unit.id,
        "house_id": house.id,
        "room_no": "101",
    }


def _api(client: httpx.Client, method: str, path: str, *, json_body=None, token: str | None = None, request_id: str):
    headers = {"X-Request-Id": request_id}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    response = client.request(method, path, json=json_body, headers=headers)
    return response


def _sms_login(client: httpx.Client, phone: str) -> dict[str, object]:
    issued = _api(
        client,
        "POST",
        "/api/v1/auth/challenge",
        json_body={"phone": phone, "purpose": "login"},
        request_id=f"prep-ch-{phone[-4:]}",
    )
    if issued.status_code != 200:
        raise RuntimeError(f"challenge {issued.status_code}")
    data = issued.json()["data"]
    sent = _api(
        client,
        "POST",
        "/api/v1/auth/sms/send",
        json_body={
            "phone": phone,
            "purpose": "login",
            "challenge_id": data["challenge_id"],
            "challenge_answer": _answer(data["prompt"]),
        },
        request_id=f"prep-send-{phone[-4:]}",
    )
    if sent.status_code != 200:
        raise RuntimeError(f"sms send {sent.status_code}")
    inbox = _api(
        client,
        "POST",
        "/api/v1/auth/sms/test-inbox",
        json_body={"phone": phone, "purpose": "login"},
        request_id=f"prep-inbox-{phone[-4:]}",
    )
    if inbox.status_code != 200:
        raise RuntimeError(f"inbox {inbox.status_code}")
    code = inbox.json()["data"]["code"]
    if not code:
        raise RuntimeError("empty inbox")
    login = _api(
        client,
        "POST",
        "/api/v1/auth/sms/login",
        json_body={"phone": phone, "code": code},
        request_id=f"prep-login-{phone[-4:]}",
    )
    if login.status_code != 200:
        raise RuntimeError(f"sms login {login.status_code}")
    return login.json()["data"]


def _patch_env_local() -> dict[str, object]:
    PRIVATE.mkdir(parents=True, exist_ok=True)
    backup = PRIVATE / "env.local.js.bak"
    if ENV_LOCAL.exists() and not backup.exists():
        shutil.copy2(ENV_LOCAL, backup)
    original = ENV_LOCAL.read_text(encoding="utf-8") if ENV_LOCAL.exists() else ""
    updated = original
    if re.search(r'develop:\s*"[^"]*"', updated):
        updated = re.sub(
            r'develop:\s*"[^"]*"',
            'develop: "http://127.0.0.1:8083/api/v1"',
            updated,
            count=1,
        )
    else:
        raise RuntimeError("env.local.js missing develop")
    if re.search(r"localPreview:\s*(true|false)", updated):
        updated = re.sub(r"localPreview:\s*(true|false)", "localPreview: false", updated, count=1)
    else:
        raise RuntimeError("env.local.js missing localPreview")
    if updated != original:
        ENV_LOCAL.write_text(updated, encoding="utf-8")
    develop = re.search(r'develop:\s*"([^"]*)"', updated)
    preview = re.search(r"localPreview:\s*(true|false)", updated)
    return {
        "changed": updated != original,
        "develop": develop.group(1) if develop else "",
        "localPreview": preview.group(1) if preview else "",
        "backup": str(backup.relative_to(REPO)).replace("\\", "/") if backup.exists() else "",
        "note": "identities/AppID untouched; localPreview false so Seed identities unused",
    }


def _catalog_contains(client: httpx.Client, token: str, property_id: int) -> dict[str, object]:
    after_id = 0
    pages = 0
    first_ids: list[int] = []
    found_page = None
    while pages < 40:
        response = _api(
            client,
            "GET",
            f"/api/v1/properties?after_id={after_id}&limit=50",
            token=token,
            request_id=f"prep-catalog-{pages}",
        )
        if response.status_code != 200:
            raise RuntimeError(f"catalog {response.status_code}")
        items = response.json()["data"]["items"]
        ids = [int(item["id"]) for item in items]
        if pages == 0:
            first_ids = ids
        pages += 1
        if property_id in ids:
            found_page = pages
            break
        if len(items) < 50:
            break
        after_id = ids[-1]
    return {
        "pages_scanned": pages,
        "first_page_count": len(first_ids),
        "first_page_contains_target": property_id in first_ids,
        "found_page": found_page,
        "first_page_index": first_ids.index(property_id) if property_id in first_ids else None,
    }


def main() -> int:
    isolated_url = _isolated_url()
    env_patch = _patch_env_local()
    engine, factory = _session_factory(isolated_url)
    session = factory()
    client = httpx.Client(base_url=API, timeout=20.0)
    try:
        with engine.connect() as connection:
            revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            property_count = connection.execute(text("SELECT COUNT(*) FROM properties")).scalar_one()
            inspector = inspect(engine)
            assignment_cols = sorted(
                column["name"] for column in inspector.get_columns("work_order_assignments")
            )
        if revision != "20260919_0009":
            raise RuntimeError(f"unexpected revision {revision}")
        org = _bootstrap_org(session)
        phones = {
            "unbound": _phone(),
            "resident": _phone(),
            "reject": _phone(),
            "tenant_admin": _phone(),
            "property_admin": _phone(),
            "staff": _phone(),
            "staff2": _phone(),
        }
        users = {role: _sms_login(client, phone) for role, phone in phones.items()}
        _grant(
            session,
            user_id=int(users["tenant_admin"]["user_id"]),
            role_code="TENANT_ADMIN",
            scope_type="TENANT",
            tenant_id=int(org["tenant_id"]),
            property_id=None,
        )
        _grant(
            session,
            user_id=int(users["property_admin"]["user_id"]),
            role_code="PROPERTY_ADMIN",
            scope_type="PROPERTY",
            tenant_id=int(org["tenant_id"]),
            property_id=int(org["property_id"]),
        )
        tenant_token = str(users["tenant_admin"]["access_token"])
        property_token = str(users["property_admin"]["access_token"])
        employee = _api(
            client,
            "POST",
            "/api/v1/admin/employees",
            json_body={
                "tenant_id": org["tenant_id"],
                "employee_no": f"E{secrets.token_hex(4)}",
                "property_id": org["property_id"],
            },
            token=tenant_token,
            request_id="prep-employee",
        )
        if employee.status_code != 200:
            raise RuntimeError(f"employee create {employee.status_code} {employee.text[:200]}")
        employee_id = employee.json()["data"]["id"]
        invite = _api(
            client,
            "POST",
            f"/api/v1/admin/employees/{employee_id}/invites",
            json_body={"phone": phones["staff"]},
            token=tenant_token,
            request_id="prep-invite",
        )
        if invite.status_code != 200:
            raise RuntimeError(f"invite {invite.status_code} {invite.text[:200]}")
        invite_token = invite.json()["data"]["invite_token"]
        property_invite = _api(
            client,
            "POST",
            f"/api/v1/admin/employees/{employee_id}/invites",
            json_body={"phone": phones["staff2"]},
            token=property_token,
            request_id="prep-property-invite",
        )
        catalog = _catalog_contains(client, str(users["resident"]["access_token"]), int(org["property_id"]))
        health = client.get("/api/v1/healthz")
        public = {
            "task_id": "MP-CURRENT-FULL-REGRESSION-001",
            "related": "AUTH-IDENTITY-AI-001-C",
            "prepared_at": _now(),
            "code_sha": "ab0b8822b34ab26c4c2960dde00c157c3b141725",
            "api": "http://127.0.0.1:8083/api/v1",
            "database": f"{ISOLATED_AUTH_HOST}:{ISOLATED_AUTH_PORT}/{ISOLATED_AUTH_DATABASE}",
            "revision": revision,
            "property_count_before_plus_one": int(property_count) + 1,
            "assignment_columns_include_assigned_source": "assigned_source" in assignment_cols,
            "provider": {
                "sms": "development",
                "llm": "development",
                "embedding": "development",
                "storage": "development_in_process_memory",
                "wechat": "development",
                "ai_enqueue_on_create": False,
            },
            "healthz": {"path": "/api/v1/healthz", "status": health.status_code},
            "env_local": env_patch,
            "org": {
                "tenant_id": org["tenant_id"],
                "property_id": org["property_id"],
                "property_name": org["property_name"],
                "building_id": org["building_id"],
                "unit_id": org["unit_id"],
                "house_id": org["house_id"],
                "room_no": org["room_no"],
            },
            "roles": {
                name: {
                    "label": name,
                    "user_id": users[name]["user_id"],
                    "phone_last4": phones[name][-4:],
                }
                for name in phones
            },
            "employee_id": employee_id,
            "property_admin_invite_status": property_invite.status_code,
            "property_admin_invite_error": (
                property_invite.json().get("error", {}).get("code")
                if property_invite.status_code != 200
                else None
            ),
            "catalog": catalog,
            "fixture_note": "测试前置准备: org/RBAC/TENANT_ADMIN+PROPERTY_ADMIN grants/employee+invite. Claim/bind/work-order left to pages.",
            "credential_location": "reports/mp-current-full-regression/accounts.local.json (gitignored)",
        }
        private = {
            "phones": phones,
            "invite_token": invite_token,
            "password": PASSWORD,
            "employee_id": employee_id,
            "org": org,
        }
        (OUT / "accounts-public.json").write_text(
            json.dumps(public, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (OUT / "accounts.local.json").write_text(
            json.dumps(private, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print("prep_ok=1", flush=True)
        print(f"revision={revision}", flush=True)
        print(f"property_id={org['property_id']}", flush=True)
        print(f"catalog_first_page={catalog['first_page_contains_target']}", flush=True)
        print(f"property_admin_invite={property_invite.status_code}", flush=True)
        print(f"env_develop={env_patch['develop']}", flush=True)
        print(f"env_localPreview={env_patch['localPreview']}", flush=True)
        return 0
    finally:
        client.close()
        session.close()
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
