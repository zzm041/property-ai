"""Focused non-destructive verification for PROPERTY-HOUSE-RELATION-006 fix round 1."""

from __future__ import annotations

from datetime import date, datetime
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

import httpx
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"
OPS = Path(__file__).resolve().parent
for candidate in (BACKEND, OPS):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from app.core.database import run_owned_transaction  # noqa: E402
from app.domain.iam import OrganizationScope, ScopeType  # noqa: E402
from app.service.dev_seed import run_development_seed  # noqa: E402
from app.service.rbac_mutation import RbacMutationService  # noqa: E402
from auth001_isolated_runtime import build_isolated_settings  # noqa: E402
from verify_property_house_relation_006 import (  # noqa: E402
    API,
    _manager_target,
    _request,
    _sms_login,
)

OUT = REPO / "reports" / "property-house-relation-006" / "fix1-runtime-evidence.json"


def _default(value: object) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    raise TypeError(f"Unsupported JSON value: {type(value).__name__}")


def _synthetic_supervisor_phone(code_sha: str) -> str:
    digits = int(hashlib.sha256(code_sha.encode("ascii")).hexdigest()[:8], 16)
    return f"+86138{digits % 100000000:08d}"


def run() -> dict[str, object]:
    code_sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=REPO, text=True
    ).strip()
    run_id = uuid4().hex[:10]
    prefix = f"phr006-fix1-{run_id}"
    settings = build_isolated_settings(enable_enqueue=True)
    target = make_url(settings.test_database_url)
    if (
        target.host != "127.0.0.1"
        or target.port != 3307
        or target.database != "property_ai_auth001_test"
    ):
        raise RuntimeError("AUTH001 target guard rejected the database")
    run_development_seed(settings, request_id=f"{prefix}-dev-seed")
    engine = create_engine(settings.test_database_url)
    with engine.connect() as connection:
        revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        if revision != "20260919_0009":
            raise RuntimeError(f"Expected 0009, found {revision}")
        manager = _manager_target(connection)

    supervisor_phone = _synthetic_supervisor_phone(code_sha)
    with httpx.Client(timeout=20.0) as client:
        health = client.get(f"{API}/healthz")
        if health.status_code != 200:
            raise RuntimeError(f"8083 health returned {health.status_code}")
        manager_session = _sms_login(
            client,
            phone=str(manager["phone_e164"]),
            request_prefix=f"{prefix}-manager",
        )
        manager_token = str(manager_session["access_token"])
        tenant_id = int(manager["tenant_id"])
        property_id = int(manager["property_id"])
        archive_before = _request(
            client,
            "GET",
            f"/admin/property-archive?tenant_id={tenant_id}&property_id={property_id}",
            token=manager_token,
            request_id=f"{prefix}-archive-before",
        )
        if len(archive_before["buildings"]) < 2:
            raise RuntimeError("At least two existing synthetic buildings are required")
        target_building = archive_before["buildings"][-1]
        duplicate_building = next(
            item for item in archive_before["buildings"] if item["id"] != target_building["id"]
        )
        target_unit = next(
            item
            for item in archive_before["units"]
            if int(item["building_id"]) == int(target_building["id"])
        )
        original_building_name = str(target_building["name"])
        original_unit_name = str(target_unit["name"])
        updated_building = _request(
            client,
            "PATCH",
            f"/admin/buildings/{target_building['id']}",
            token=manager_token,
            request_id=f"{prefix}-building-update",
            json_body={"name": f"{original_building_name}·API校验"},
        )
        updated_unit = _request(
            client,
            "PATCH",
            f"/admin/units/{target_unit['id']}",
            token=manager_token,
            request_id=f"{prefix}-unit-update",
            json_body={"name": f"{original_unit_name}·API校验"},
        )
        _request(
            client,
            "PATCH",
            f"/admin/buildings/{target_building['id']}",
            expected=409,
            token=manager_token,
            request_id=f"{prefix}-building-duplicate",
            json_body={"code": duplicate_building["code"]},
        )
        second_unit = _request(
            client,
            "POST",
            "/admin/units",
            token=manager_token,
            request_id=f"{prefix}-second-unit",
            json_body={
                "tenant_id": tenant_id,
                "property_id": property_id,
                "building_id": target_building["id"],
                "code": f"D{run_id.upper()}",
                "name": f"重复约束单元 {run_id}",
            },
        )
        _request(
            client,
            "PATCH",
            f"/admin/units/{target_unit['id']}",
            expected=409,
            token=manager_token,
            request_id=f"{prefix}-unit-duplicate",
            json_body={"code": second_unit["code"]},
        )

        building_conflict_code = f"CB{run_id.upper()}"
        building_conflict_rows = [
            {
                "building_code": building_conflict_code,
                "building_name": "缓存楼栋甲",
                "unit_code": "U1",
                "unit_name": "缓存单元",
                "room_no": "101",
                "floor": 1,
                "status": "ACTIVE",
            },
            {
                "building_code": building_conflict_code,
                "building_name": "缓存楼栋乙",
                "unit_code": "U1",
                "unit_name": "缓存单元",
                "room_no": "102",
                "floor": 1,
                "status": "ACTIVE",
            },
        ]
        _request(
            client,
            "POST",
            "/admin/houses/import",
            expected=409,
            token=manager_token,
            request_id=f"{prefix}-cached-building-conflict",
            json_body={
                "tenant_id": tenant_id,
                "property_id": property_id,
                "rows": building_conflict_rows,
            },
        )
        unit_conflict_code = f"CU{run_id.upper()}"
        unit_conflict_rows = [
            {
                "building_code": unit_conflict_code,
                "building_name": "单元冲突楼栋",
                "unit_code": "U1",
                "unit_name": "缓存单元甲",
                "room_no": "101",
                "floor": 1,
                "status": "ACTIVE",
            },
            {
                "building_code": unit_conflict_code,
                "building_name": "单元冲突楼栋",
                "unit_code": "U1",
                "unit_name": "缓存单元乙",
                "room_no": "102",
                "floor": 1,
                "status": "ACTIVE",
            },
        ]
        _request(
            client,
            "POST",
            "/admin/houses/import",
            expected=409,
            token=manager_token,
            request_id=f"{prefix}-cached-unit-conflict",
            json_body={
                "tenant_id": tenant_id,
                "property_id": property_id,
                "rows": unit_conflict_rows,
            },
        )

        supervisor_session = _sms_login(
            client,
            phone=supervisor_phone,
            request_prefix=f"{prefix}-supervisor-register",
        )
        supervisor_user_id = int(supervisor_session["user_id"])
        with Session(engine) as session:
            fixture_service = RbacMutationService(session)
            run_owned_transaction(
                session,
                lambda: fixture_service.grant_user_role_for_development_seed(
                    target_user_id=supervisor_user_id,
                    role_code="SUPERVISOR",
                    scope=OrganizationScope(
                        ScopeType.PROPERTY,
                        tenant_id=tenant_id,
                        property_id=property_id,
                    ),
                    request_id=f"{prefix}-supervisor-fixture",
                ),
            )
        supervisor_token = str(supervisor_session["access_token"])
        supervisor_me = _request(
            client,
            "GET",
            "/me",
            token=supervisor_token,
            request_id=f"{prefix}-supervisor-me",
        )
        supervisor_archive = _request(
            client,
            "GET",
            f"/admin/property-archive?tenant_id={tenant_id}&property_id={property_id}",
            token=supervisor_token,
            request_id=f"{prefix}-supervisor-read",
        )
        _request(
            client,
            "PATCH",
            f"/admin/buildings/{target_building['id']}",
            expected=403,
            token=supervisor_token,
            request_id=f"{prefix}-supervisor-write-denied",
            json_body={"name": "禁止写入"},
        )
        archive_after = _request(
            client,
            "GET",
            f"/admin/property-archive?tenant_id={tenant_id}&property_id={property_id}",
            token=manager_token,
            request_id=f"{prefix}-archive-after",
        )

    final_building = next(
        item for item in archive_after["buildings"] if item["id"] == target_building["id"]
    )
    final_unit = next(item for item in archive_after["units"] if item["id"] == target_unit["id"])
    with engine.connect() as connection:
        rollback_counts = {
            "building_name_conflict": connection.execute(
                text(
                    "SELECT COUNT(*) FROM buildings WHERE tenant_id=:tenant_id "
                    "AND property_id=:property_id AND code=:code"
                ),
                {"tenant_id": tenant_id, "property_id": property_id, "code": building_conflict_code},
            ).scalar_one(),
            "unit_name_conflict": connection.execute(
                text(
                    "SELECT COUNT(*) FROM buildings WHERE tenant_id=:tenant_id "
                    "AND property_id=:property_id AND code=:code"
                ),
                {"tenant_id": tenant_id, "property_id": property_id, "code": unit_conflict_code},
            ).scalar_one(),
        }
        audit_actions = [
            row[0]
            for row in connection.execute(
                text("SELECT action FROM audit_logs WHERE request_id LIKE :prefix ORDER BY id"),
                {"prefix": f"{prefix}%"},
            )
        ]

    assertions = {
        "migration_is_0009": revision == "20260919_0009",
        "manager_official_sms_session": bool(manager_session.get("access_token")),
        "building_update_succeeded": updated_building["name"].endswith("·API校验"),
        "unit_update_succeeded": updated_unit["name"].endswith("·API校验"),
        "building_duplicate_code_rejected_without_change": final_building["code"]
        == target_building["code"],
        "unit_duplicate_code_rejected_without_change": final_unit["code"] == target_unit["code"],
        "cached_building_name_conflict_rolled_back": rollback_counts["building_name_conflict"] == 0,
        "cached_unit_name_conflict_rolled_back": rollback_counts["unit_name_conflict"] == 0,
        "building_update_audited": "PROPERTY_BUILDING_UPDATED" in audit_actions,
        "unit_update_audited": "PROPERTY_UNIT_UPDATED" in audit_actions,
        "supervisor_has_property_read": "property:read" in supervisor_me.get("permissions", []),
        "supervisor_lacks_property_manage": "property:manage" not in supervisor_me.get("permissions", []),
        "supervisor_read_archive": len(supervisor_archive["buildings"]) > 0,
        "supervisor_write_denied": True,
    }
    if not all(assertions.values()):
        raise RuntimeError(f"Fix verification assertion failed: {assertions}")
    return {
        "task_id": "PROPERTY-HOUSE-RELATION-006-FIX-1",
        "run_id": run_id,
        "code_sha": code_sha,
        "environment": {
            "api": API,
            "database": "127.0.0.1:3307/property_ai_auth001_test",
            "migration": revision,
            "provider": "development SMS substitute",
        },
        "actor_modes": {
            "manager": "official SMS PROPERTY_ADMIN; not SYSTEM",
            "read_only": "official SMS SUPERVISOR prepared by authorized development fixture",
            "raw_phones_saved": False,
            "tokens_saved": False,
        },
        "objects": {
            "tenant_id": tenant_id,
            "property_id": property_id,
            "updated_building_id": int(target_building["id"]),
            "updated_unit_id": int(target_unit["id"]),
            "constraint_unit_id": int(second_unit["id"]),
        },
        "rollback_counts": rollback_counts,
        "audit_actions": sorted(set(audit_actions)),
        "assertions": assertions,
        "status": "PASS",
        "notes": [
            "All business mutations used authenticated APIs except the explicitly labeled development role fixture.",
            "SQL after API calls was read-only evidence; no tables were cleared and no migration ran.",
            "This is local software evidence, not production-provider or Production V1 acceptance.",
        ],
    }


if __name__ == "__main__":
    result = run()
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=_default), encoding="utf-8")
    print(json.dumps({"task_id": result["task_id"], "status": result["status"], "assertions": result["assertions"]}, ensure_ascii=False))
