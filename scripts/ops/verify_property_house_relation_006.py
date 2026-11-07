"""Non-destructive AUTH001 runtime verification for PROPERTY-HOUSE-RELATION-006."""

from __future__ import annotations

import argparse
from datetime import date, datetime
import json
from pathlib import Path
import re
import subprocess
import sys
from uuid import uuid4

import httpx
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"
OPS = Path(__file__).resolve().parent
for candidate in (BACKEND, OPS):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from app.domain.phone import mask_phone  # noqa: E402
from app.service.dev_seed import run_development_seed  # noqa: E402
from auth001_isolated_runtime import build_isolated_settings  # noqa: E402

API = "http://127.0.0.1:8083/api/v1"


def _default(value: object) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    raise TypeError(f"Unsupported JSON value: {type(value).__name__}")


def _request(
    client: httpx.Client,
    method: str,
    path: str,
    *,
    expected: int = 200,
    token: str | None = None,
    request_id: str,
    json_body: dict[str, object] | None = None,
) -> dict[str, object]:
    headers = {"X-Request-Id": request_id}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    response = client.request(
        method,
        f"{API}{path}",
        headers=headers,
        json=json_body,
    )
    if response.status_code != expected:
        error = response.json().get("error", {}) if response.content else {}
        raise RuntimeError(
            f"{method} {path} returned {response.status_code}, "
            f"expected {expected}: {error.get('code')}"
        )
    payload = response.json()
    data = payload.get("data", {})
    if not isinstance(data, dict):
        raise RuntimeError(f"{method} {path} returned an invalid data envelope")
    return data


def _sms_login(
    client: httpx.Client,
    *,
    phone: str,
    request_prefix: str,
) -> dict[str, object]:
    challenge = _request(
        client,
        "POST",
        "/auth/challenge",
        request_id=f"{request_prefix}-challenge",
        json_body={"phone": phone, "purpose": "login"},
    )
    operands = [int(value) for value in re.findall(r"\d+", str(challenge["prompt"]))]
    if len(operands) != 2:
        raise RuntimeError("Visual challenge prompt was not recognized")
    _request(
        client,
        "POST",
        "/auth/sms/send",
        request_id=f"{request_prefix}-send",
        json_body={
            "phone": phone,
            "purpose": "login",
            "challenge_id": challenge["challenge_id"],
            "challenge_answer": str(sum(operands)),
        },
    )
    inbox = _request(
        client,
        "POST",
        "/auth/sms/test-inbox",
        request_id=f"{request_prefix}-inbox",
        json_body={"phone": phone, "purpose": "login"},
    )
    session = _request(
        client,
        "POST",
        "/auth/sms/login",
        request_id=f"{request_prefix}-login",
        json_body={"phone": phone, "code": inbox["code"]},
    )
    if not session.get("access_token") or not session.get("refresh_token"):
        raise RuntimeError("Official SMS login did not issue a complete session")
    return session


def _manager_target(connection) -> dict[str, object]:
    row = connection.execute(
        text(
            "SELECT u.id AS user_id, up.phone_e164, ur.tenant_id, "
            "COALESCE(ur.property_id, "
            "(SELECT MIN(p.id) FROM properties p WHERE p.tenant_id = ur.tenant_id)) "
            "AS property_id, r.code AS role_code "
            "FROM user_roles ur "
            "JOIN roles r ON r.id = ur.role_id "
            "JOIN users u ON u.id = ur.user_id AND BINARY u.status = 'ACTIVE' "
            "JOIN user_phones up ON up.user_id = u.id AND BINARY up.status = 'ACTIVE' "
            "WHERE r.code IN ('PROPERTY_ADMIN', 'TENANT_ADMIN') "
            "ORDER BY CASE r.code WHEN 'PROPERTY_ADMIN' THEN 0 ELSE 1 END, ur.id "
            "LIMIT 1"
        )
    ).mappings().first()
    if row is None or row["property_id"] is None:
        raise RuntimeError("No official-login property manager is available")
    return dict(row)


def run() -> dict[str, object]:
    run_id = uuid4().hex[:10]
    request_prefix = f"phr006-{run_id}"
    settings = build_isolated_settings(enable_enqueue=True)
    target = make_url(settings.test_database_url)
    if (
        target.host != "127.0.0.1"
        or target.port != 3307
        or target.database != "property_ai_auth001_test"
    ):
        raise RuntimeError("AUTH001 target guard rejected the database")

    seed_request_id = f"{request_prefix}-dev-seed"
    run_development_seed(settings, request_id=seed_request_id)
    engine = create_engine(settings.test_database_url)
    with engine.connect() as connection:
        revision = connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one()
        if revision != "20260919_0009":
            raise RuntimeError(f"Expected 0009, found {revision}")
        manager = _manager_target(connection)
        other_property_id = connection.execute(
            text(
                "SELECT MIN(id) FROM properties "
                "WHERE id <> :property_id AND tenant_id <> :tenant_id"
            ),
            {
                "property_id": manager["property_id"],
                "tenant_id": manager["tenant_id"],
            },
        ).scalar_one_or_none()

    building_code = f"PHR{run_id.upper()}"
    import_building_code = f"{building_code}I"
    resident_phone = f"+86139{int(uuid4().hex[:8], 16) % 100000000:08d}"
    with httpx.Client(timeout=20.0) as client:
        health = client.get(f"{API}/healthz")
        if health.status_code != 200:
            raise RuntimeError(f"8083 health returned {health.status_code}")
        manager_session = _sms_login(
            client,
            phone=str(manager["phone_e164"]),
            request_prefix=f"{request_prefix}-manager",
        )
        manager_token = str(manager_session["access_token"])
        me = _request(
            client,
            "GET",
            "/me",
            token=manager_token,
            request_id=f"{request_prefix}-manager-me",
        )
        if "property:manage" not in me.get("permissions", []):
            raise RuntimeError("Manager session is missing property:manage")

        tenant_id = int(manager["tenant_id"])
        property_id = int(manager["property_id"])
        building = _request(
            client,
            "POST",
            "/admin/buildings",
            token=manager_token,
            request_id=f"{request_prefix}-building",
            json_body={
                "tenant_id": tenant_id,
                "property_id": property_id,
                "code": building_code,
                "name": f"合成楼栋 {run_id}",
            },
        )
        unit = _request(
            client,
            "POST",
            "/admin/units",
            token=manager_token,
            request_id=f"{request_prefix}-unit",
            json_body={
                "tenant_id": tenant_id,
                "property_id": property_id,
                "building_id": building["id"],
                "code": "U1",
                "name": "合成一单元",
            },
        )
        house = _request(
            client,
            "POST",
            "/admin/houses",
            token=manager_token,
            request_id=f"{request_prefix}-house",
            json_body={
                "tenant_id": tenant_id,
                "property_id": property_id,
                "unit_id": unit["id"],
                "room_no": "101",
                "floor": 1,
                "status": "ACTIVE",
            },
        )
        inactive = _request(
            client,
            "PATCH",
            f"/admin/houses/{house['id']}",
            token=manager_token,
            request_id=f"{request_prefix}-house-inactive",
            json_body={"status": "INACTIVE"},
        )
        active = _request(
            client,
            "PATCH",
            f"/admin/houses/{house['id']}",
            token=manager_token,
            request_id=f"{request_prefix}-house-active",
            json_body={"status": "ACTIVE"},
        )

        import_rows = [
            {
                "building_code": import_building_code,
                "building_name": f"导入楼栋 {run_id}",
                "unit_code": "IU1",
                "unit_name": "导入一单元",
                "room_no": "201",
                "floor": 2,
                "status": "ACTIVE",
            },
            {
                "building_code": import_building_code,
                "building_name": f"导入楼栋 {run_id}",
                "unit_code": "IU1",
                "unit_name": "导入一单元",
                "room_no": "202",
                "floor": 2,
                "status": "ACTIVE",
            },
        ]
        imported = _request(
            client,
            "POST",
            "/admin/houses/import",
            token=manager_token,
            request_id=f"{request_prefix}-import",
            json_body={
                "tenant_id": tenant_id,
                "property_id": property_id,
                "rows": import_rows,
            },
        )
        retried = _request(
            client,
            "POST",
            "/admin/houses/import",
            token=manager_token,
            request_id=f"{request_prefix}-import-retry",
            json_body={
                "tenant_id": tenant_id,
                "property_id": property_id,
                "rows": import_rows,
            },
        )
        conflict_rows = [
            {
                **import_rows[0],
                "room_no": "203",
                "floor": 2,
            },
            {
                **import_rows[0],
                "floor": 9,
            },
        ]
        conflict = _request(
            client,
            "POST",
            "/admin/houses/import",
            expected=409,
            token=manager_token,
            request_id=f"{request_prefix}-import-conflict",
            json_body={
                "tenant_id": tenant_id,
                "property_id": property_id,
                "rows": conflict_rows,
            },
        )
        del conflict
        exported = _request(
            client,
            "GET",
            f"/admin/houses/export?tenant_id={tenant_id}&property_id={property_id}",
            token=manager_token,
            request_id=f"{request_prefix}-export",
        )
        archive = _request(
            client,
            "GET",
            f"/admin/property-archive?tenant_id={tenant_id}&property_id={property_id}",
            token=manager_token,
            request_id=f"{request_prefix}-archive",
        )

        cross_scope_denied = False
        if other_property_id is not None:
            response = client.get(
                f"{API}/admin/property-archive",
                params={
                    "tenant_id": -1,
                    "property_id": int(other_property_id),
                },
                headers={
                    "Authorization": f"Bearer {manager_token}",
                    "X-Request-Id": f"{request_prefix}-cross-scope",
                },
            )
            # Use the authoritative tenant below; the -1 request proves validation only.
            if response.status_code != 422:
                raise RuntimeError("Invalid tenant guard did not reject the request")
            with engine.connect() as connection:
                other_tenant_id = connection.execute(
                    text("SELECT tenant_id FROM properties WHERE id = :property_id"),
                    {"property_id": int(other_property_id)},
                ).scalar_one()
            response = client.get(
                f"{API}/admin/property-archive",
                params={
                    "tenant_id": int(other_tenant_id),
                    "property_id": int(other_property_id),
                },
                headers={
                    "Authorization": f"Bearer {manager_token}",
                    "X-Request-Id": f"{request_prefix}-cross-scope-real",
                },
            )
            cross_scope_denied = response.status_code == 403

        resident_session = _sms_login(
            client,
            phone=resident_phone,
            request_prefix=f"{request_prefix}-resident",
        )
        resident_token = str(resident_session["access_token"])
        bind_request = _request(
            client,
            "POST",
            "/resident-bind-requests",
            token=resident_token,
            request_id=f"{request_prefix}-bind",
            json_body={
                "house_id": house["id"],
                "tenant_id": tenant_id,
                "property_id": property_id,
                "relation_type": "OWNER",
            },
        )
        _request(
            client,
            "POST",
            f"/admin/resident-bind-requests/{bind_request['id']}/approve",
            token=manager_token,
            request_id=f"{request_prefix}-approve",
            json_body={},
        )
        resident_houses_before = _request(
            client,
            "GET",
            "/me/houses",
            token=resident_token,
            request_id=f"{request_prefix}-resident-houses-before",
        )
        history_before = _request(
            client,
            "GET",
            f"/admin/resident-house-relations?tenant_id={tenant_id}"
            f"&property_id={property_id}&house_id={house['id']}",
            token=manager_token,
            request_id=f"{request_prefix}-history-before",
        )
        relation = next(
            item
            for item in history_before["items"]
            if int(item["user_id"]) == int(resident_session["user_id"])
            and item["status"] == "ACTIVE"
        )
        _request(
            client,
            "POST",
            f"/admin/resident-house-relations/{relation['id']}/end",
            token=manager_token,
            request_id=f"{request_prefix}-relation-end",
            json_body={"status": "ENDED"},
        )
        resident_houses_after = _request(
            client,
            "GET",
            "/me/houses",
            token=resident_token,
            request_id=f"{request_prefix}-resident-houses-after",
        )
        history_after = _request(
            client,
            "GET",
            f"/admin/resident-house-relations?tenant_id={tenant_id}"
            f"&property_id={property_id}&house_id={house['id']}",
            token=manager_token,
            request_id=f"{request_prefix}-history-after",
        )

    export_rows = list(exported["rows"])
    archive_house_ids = {int(item["id"]) for item in archive["houses"]}
    ended_relation = next(
        item for item in history_after["items"] if int(item["id"]) == int(relation["id"])
    )
    with engine.connect() as connection:
        audit_actions = [
            row[0]
            for row in connection.execute(
                text(
                    "SELECT action FROM audit_logs "
                    "WHERE request_id LIKE :prefix ORDER BY id"
                ),
                {"prefix": f"{request_prefix}%"},
            )
        ]
        rolled_back_count = connection.execute(
            text(
                "SELECT COUNT(*) FROM houses h "
                "JOIN units u ON u.id = h.unit_id "
                "JOIN buildings b ON b.id = u.building_id "
                "WHERE h.tenant_id = :tenant_id AND h.property_id = :property_id "
                "AND b.code = :building_code AND h.room_no = '203'"
            ),
            {
                "tenant_id": int(manager["tenant_id"]),
                "property_id": int(manager["property_id"]),
                "building_code": import_building_code,
            },
        ).scalar_one()
    engine.dispose()

    assertions = {
        "migration_is_0009": revision == "20260919_0009",
        "manager_used_official_sms_session": manager_session["auth_method"] == "sms",
        "manager_has_property_manage": "property:manage" in me.get("permissions", []),
        "property_admin_not_system": manager["role_code"] != "SYSTEM_ADMIN",
        "direct_house_status_round_trip": inactive["status"] == "INACTIVE"
        and active["status"] == "ACTIVE",
        "archive_contains_created_house": int(house["id"]) in archive_house_ids,
        "import_created_expected_hierarchy": imported
        == {
            "created_buildings": 1,
            "created_units": 1,
            "created_houses": 2,
            "unchanged_houses": 0,
        },
        "import_retry_is_unchanged": retried
        == {
            "created_buildings": 0,
            "created_units": 0,
            "created_houses": 0,
            "unchanged_houses": 2,
        },
        "conflicting_import_rolled_back_first_row": int(rolled_back_count) == 0,
        "export_contains_imported_rows": sum(
            row["building_code"] == import_building_code for row in export_rows
        )
        == 2,
        "cross_scope_denied": cross_scope_denied is True,
        "resident_used_official_sms_session": resident_session["auth_method"] == "sms",
        "approved_relation_visible_to_resident": any(
            int(item["house_id"]) == int(house["id"])
            for item in resident_houses_before["items"]
        ),
        "ended_relation_removed_from_current_view": all(
            int(item["house_id"]) != int(house["id"])
            for item in resident_houses_after["items"]
        ),
        "same_relation_history_row_retained": ended_relation["status"] == "ENDED"
        and ended_relation["valid_to"] is not None,
        "property_and_relation_audits_present": {
            "PROPERTY_BUILDING_CREATED",
            "PROPERTY_UNIT_CREATED",
            "PROPERTY_HOUSE_CREATED",
            "PROPERTY_HOUSE_UPDATED",
            "RESIDENT_HOUSE_RELATION_ENDED",
        }.issubset(set(audit_actions)),
    }
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return {
        "task_id": "PROPERTY-HOUSE-RELATION-006",
        "run_id": run_id,
        "code_sha": head,
        "environment": {
            "api": API,
            "database": "127.0.0.1:3307/property_ai_auth001_test",
            "migration": revision,
            "mode": "official SMS sessions plus normal business APIs",
            "model": "not involved",
        },
        "synthetic_identity": {
            "manager_role": manager["role_code"],
            "manager_phone_masked": mask_phone(str(manager["phone_e164"])),
            "resident_phone_masked": mask_phone(resident_phone),
        },
        "objects": {
            "tenant_id": int(manager["tenant_id"]),
            "property_id": int(manager["property_id"]),
            "building_id": int(building["id"]),
            "unit_id": int(unit["id"]),
            "house_id": int(house["id"]),
            "bind_request_id": int(bind_request["id"]),
            "relation_id": int(relation["id"]),
        },
        "import": {"first": imported, "retry": retried},
        "audit_actions": sorted(set(audit_actions)),
        "assertions": assertions,
        "status": "PASS" if all(assertions.values()) else "FAIL",
        "notes": [
            "No generic integration fixture, table cleanup, direct status SQL, or migration was used.",
            "All business mutations used the 8083 API; SQL after the run was read-only evidence.",
            "Synthetic records are retained for traceability and were not deleted after verification.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    evidence = run()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2, default=_default) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(evidence["assertions"], ensure_ascii=False, sort_keys=True))
    if evidence["status"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
