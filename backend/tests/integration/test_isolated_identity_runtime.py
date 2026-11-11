"""Official-session resident bind and employee claim on the exclusive 0009 schema.

Requires AUTH_ISOLATED_DATABASE_URL. Absence is skip, not PASS.
Does not use Seed login, SYSTEM grants, or SQL-stamped APPROVED/ACTIVE relations.
"""

from __future__ import annotations

import secrets

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.core.database import create_database_engine, run_owned_transaction
from app.domain.dev import SEED_PERMISSIONS, SEED_ROLE_PERMISSIONS
from app.main import create_app
from app.model.identity import Permission, Role, RolePermission, UserRole
from app.service.identity.property import PropertyService
from tests.integration.test_isolated_auth_runtime import (
    ISOLATED_URL,
    _error,
    _inbox,
    _issue,
    _phone,
    _send,
    _settings,
)

pytestmark = pytest.mark.skipif(
    not ISOLATED_URL,
    reason=(
        "AUTH_ISOLATED_DATABASE_URL not provisioned; "
        "isolated identity runtime is not claimed as PASS"
    ),
)

ROLE_CODES = ("RESIDENT", "TENANT_ADMIN", "STAFF")
NEW_PASSWORD = "goodpass1"


@pytest.fixture(scope="module")
def isolated_settings():
    return _settings()


@pytest.fixture(scope="module")
def isolated_client(isolated_settings):
    with TestClient(create_app(isolated_settings)) as client:
        yield client


def _auth(token: str, request_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "X-Request-Id": request_id}


def _items(client: TestClient, url: str, token: str, request_id: str) -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    after_id = 0
    for _ in range(20):
        response = client.get(
            url,
            params={"after_id": after_id, "limit": 100},
            headers=_auth(token, request_id),
        )
        assert response.status_code == 200, response.text
        batch = response.json()["data"]["items"]
        items.extend(batch)
        if len(batch) < 100:
            break
        after_id = int(batch[-1]["id"])
    return items


def _sms_login(client: TestClient, phone: str) -> dict[str, object]:
    challenge_id, answer = _issue(client, phone, "login")
    sent = _send(client, phone, "login", challenge_id, answer)
    assert sent.status_code == 200, sent.text
    code = _inbox(client, phone, "login")
    login = client.post(
        "/api/v1/auth/sms/login",
        json={"phone": phone, "code": code},
        headers={"X-Request-Id": f"login-{phone[-4:]}"},
    )
    assert login.status_code == 200, login.text
    payload = login.json()["data"]
    assert payload["auth_method"] == "sms"
    assert payload["user_id"] > 0
    return payload


def _session() -> tuple[object, Session]:
    engine = create_database_engine(ISOLATED_URL)
    factory = sessionmaker(
        bind=engine,
        class_=Session,
        autoflush=False,
        expire_on_commit=False,
    )
    return engine, factory()


def _ensure_rbac_catalog(session: Session) -> dict[str, Role]:
    def _persist() -> dict[str, Role]:
        roles: dict[str, Role] = {}
        for code in ROLE_CODES:
            role = session.scalar(select(Role).where(Role.code == code))
            if role is None:
                role = Role(code=code, name=code.replace("_", " ").title())
                session.add(role)
                session.flush()
            roles[code] = role
        permissions: dict[str, Permission] = {}
        needed = {
            code
            for role_code in ROLE_CODES
            for code in SEED_ROLE_PERMISSIONS[role_code]
        }
        names = {item.code: item.name for item in SEED_PERMISSIONS}
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


def _grant_tenant_admin(session: Session, *, user_id: int, tenant_id: int) -> None:
    def _persist() -> None:
        role = session.scalar(select(Role).where(Role.code == "TENANT_ADMIN"))
        assert role is not None
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

    run_owned_transaction(session, _persist)


def _bootstrap_org() -> dict[str, int]:
    engine, session = _session()
    try:
        suffix = secrets.token_hex(3)
        properties = PropertyService(session)
        tenant = properties.create_tenant(name=f"auth001-id-tenant-{suffix}")
        prop = properties.create_property(
            tenant_id=tenant.id,
            name=f"auth001-id-property-{suffix}",
            address="Isolated identity street",
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
        _ensure_rbac_catalog(session)
        return {
            "tenant_id": tenant.id,
            "property_id": prop.id,
            "building_id": building.id,
            "unit_id": unit.id,
            "house_id": house.id,
        }
    finally:
        session.close()
        engine.dispose()


def test_official_sessions_complete_bind_claim_and_lowercase_reset(
    isolated_client: TestClient,
    isolated_settings,
) -> None:
    del isolated_settings
    org = _bootstrap_org()
    resident_phone = _phone()
    manager_phone = _phone()
    staff_phone = _phone()
    resident = _sms_login(isolated_client, resident_phone)
    manager = _sms_login(isolated_client, manager_phone)
    staff = _sms_login(isolated_client, staff_phone)

    engine, session = _session()
    try:
        _grant_tenant_admin(
            session,
            user_id=int(manager["user_id"]),
            tenant_id=org["tenant_id"],
        )
    finally:
        session.close()
        engine.dispose()

    profile = isolated_client.patch(
        "/api/v1/me/profile",
        json={"display_name": "住户甲"},
        headers=_auth(str(resident["access_token"]), "profile"),
    )
    assert profile.status_code == 200, profile.text
    assert profile.json()["data"]["display_name"] == "住户甲"
    assert profile.json()["data"]["profile_completed"] is True

    property_ids = [
        item["id"]
        for item in _items(
            isolated_client,
            "/api/v1/properties",
            str(resident["access_token"]),
            "catalog-properties",
        )
    ]
    assert org["property_id"] in property_ids
    building_ids = [
        item["id"]
        for item in _items(
            isolated_client,
            f"/api/v1/properties/{org['property_id']}/buildings",
            str(resident["access_token"]),
            "catalog-buildings",
        )
    ]
    assert org["building_id"] in building_ids
    unit_ids = [
        item["id"]
        for item in _items(
            isolated_client,
            f"/api/v1/buildings/{org['building_id']}/units",
            str(resident["access_token"]),
            "catalog-units",
        )
    ]
    assert org["unit_id"] in unit_ids
    house_ids = [
        item["id"]
        for item in _items(
            isolated_client,
            f"/api/v1/units/{org['unit_id']}/houses",
            str(resident["access_token"]),
            "catalog-houses",
        )
    ]
    assert org["house_id"] in house_ids

    bind = isolated_client.post(
        "/api/v1/resident-bind-requests",
        json={
            "house_id": org["house_id"],
            "relation_type": "OWNER",
            "tenant_id": org["tenant_id"],
            "property_id": org["property_id"],
        },
        headers=_auth(str(resident["access_token"]), "bind-create"),
    )
    assert bind.status_code == 200, bind.text
    bind_id = bind.json()["data"]["id"]
    assert bind.json()["data"]["status"] == "PENDING"

    manager_me = isolated_client.get(
        "/api/v1/me",
        headers=_auth(str(manager["access_token"]), "manager-me"),
    )
    assert manager_me.status_code == 200, manager_me.text
    manager_payload = manager_me.json()["data"]
    assert "TENANT_ADMIN" in manager_payload["roles"]
    assert "SYSTEM_ADMIN" not in manager_payload["roles"]
    assert "employee:manage" in manager_payload["permissions"]
    assert "resident_binding:review" in manager_payload["permissions"]
    assert "work_order:dispatch" in manager_payload["permissions"]

    approve = isolated_client.post(
        f"/api/v1/admin/resident-bind-requests/{bind_id}/approve",
        headers=_auth(str(manager["access_token"]), "bind-approve"),
    )
    assert approve.status_code == 200, approve.text
    assert approve.json()["data"]["status"] == "APPROVED"

    my_houses = isolated_client.get(
        "/api/v1/me/houses",
        headers=_auth(str(resident["access_token"]), "my-houses"),
    )
    assert my_houses.status_code == 200, my_houses.text
    house_items = my_houses.json()["data"]["items"]
    assert any(
        item["house_id"] == org["house_id"] and item["status"] == "ACTIVE"
        for item in house_items
    )
    resident_me = isolated_client.get(
        "/api/v1/me",
        headers=_auth(str(resident["access_token"]), "resident-me"),
    )
    assert resident_me.status_code == 200
    resident_payload = resident_me.json()["data"]
    assert "RESIDENT" in resident_payload["roles"]
    assert "work_order:create" in resident_payload["permissions"]
    assert "SYSTEM_ADMIN" not in resident_payload["roles"]

    employee = isolated_client.post(
        "/api/v1/admin/employees",
        json={
            "tenant_id": org["tenant_id"],
            "employee_no": f"E{secrets.token_hex(4)}",
            "property_id": org["property_id"],
        },
        headers=_auth(str(manager["access_token"]), "employee-create"),
    )
    assert employee.status_code == 200, employee.text
    assert employee.json()["data"]["user_id"] is None
    employee_id = employee.json()["data"]["id"]
    invite = isolated_client.post(
        f"/api/v1/admin/employees/{employee_id}/invites",
        json={"phone": staff_phone},
        headers=_auth(str(manager["access_token"]), "employee-invite"),
    )
    assert invite.status_code == 200, invite.text
    token = invite.json()["data"]["invite_token"]
    assert token
    claim = isolated_client.post(
        "/api/v1/staff/employee-claim",
        json={"token": token},
        headers=_auth(str(staff["access_token"]), "employee-claim"),
    )
    assert claim.status_code == 200, claim.text
    assert claim.json()["data"]["user_id"] == staff["user_id"]
    staff_me = isolated_client.get(
        "/api/v1/me",
        headers=_auth(str(staff["access_token"]), "staff-me"),
    )
    assert staff_me.status_code == 200, staff_me.text
    staff_payload = staff_me.json()["data"]
    assert "STAFF" in staff_payload["roles"]
    assert "work_order:process" in staff_payload["permissions"]
    staff_orders = isolated_client.get(
        "/api/v1/staff/work-orders",
        headers=_auth(str(staff["access_token"]), "staff-orders"),
    )
    assert staff_orders.status_code == 200, staff_orders.text
    assert staff_orders.json()["data"]["items"] == []

    reset_id, reset_answer = _issue(isolated_client, resident_phone, "reset")
    reset_send = _send(isolated_client, resident_phone, "reset", reset_id, reset_answer)
    assert reset_send.status_code == 200, reset_send.text
    reset_code = _inbox(isolated_client, resident_phone, "reset")
    verify = isolated_client.post(
        "/api/v1/auth/password/reset/verify",
        json={"phone": resident_phone, "code": reset_code},
        headers={"X-Request-Id": "reset-verify-lower"},
    )
    assert verify.status_code == 200, verify.text
    reset_token = verify.json()["data"]["reset_token"]
    confirm = isolated_client.post(
        "/api/v1/auth/password/reset/confirm",
        json={
            "reset_token": reset_token,
            "new_password": NEW_PASSWORD,
            "confirm_password": NEW_PASSWORD,
        },
        headers={"X-Request-Id": "reset-confirm-lower"},
    )
    assert confirm.status_code == 200, confirm.text
    stale = isolated_client.get(
        "/api/v1/me",
        headers=_auth(str(resident["access_token"]), "revoked-after-reset"),
    )
    assert stale.status_code == 401
    stale_refresh = isolated_client.post(
        "/api/v1/auth/token/refresh",
        json={"refresh_token": resident["refresh_token"]},
        headers={"X-Request-Id": "refresh-after-reset"},
    )
    assert stale_refresh.status_code == 401
    folded = isolated_client.post(
        "/api/v1/auth/password/login",
        json={"phone": resident_phone, "password": NEW_PASSWORD.upper()},
        headers={"X-Request-Id": "login-folded"},
    )
    assert folded.status_code == 401
    assert _error(folded) == "AUTH.INVALID_PASSWORD"
    password_login = isolated_client.post(
        "/api/v1/auth/password/login",
        json={"phone": resident_phone, "password": NEW_PASSWORD},
        headers={"X-Request-Id": "login-lower"},
    )
    assert password_login.status_code == 200, password_login.text
    assert password_login.json()["data"]["auth_method"] == "password"
    relogin_me = isolated_client.get(
        "/api/v1/me",
        headers=_auth(password_login.json()["data"]["access_token"], "relogin-me"),
    )
    assert relogin_me.status_code == 200
    assert relogin_me.json()["data"]["user_id"] == resident["user_id"]
    assert "RESIDENT" in relogin_me.json()["data"]["roles"]
