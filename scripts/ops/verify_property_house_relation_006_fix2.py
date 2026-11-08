"""Two-session service interleaving verification for PHR006-B4."""

from __future__ import annotations

from datetime import date, datetime
import json
from pathlib import Path
import subprocess
import sys
from typing import Callable
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

from app.repository.identity import IdentityRepository  # noqa: E402
from app.repository.property import PropertyRepository  # noqa: E402
from app.service.auth import AuthService  # noqa: E402
from app.service.property_admin import PropertyAdminService  # noqa: E402
from auth001_isolated_runtime import build_isolated_settings  # noqa: E402
from verify_property_house_relation_006 import (  # noqa: E402
    API,
    _manager_target,
    _request,
    _sms_login,
)

OUT = REPO / "reports" / "property-house-relation-006" / "fix2-concurrency-evidence.json"


class InterleavingPropertyRepository(PropertyRepository):
    """Run one competing service transaction immediately before the requested lock."""

    def __init__(
        self,
        session: Session,
        *,
        before_building_lock: Callable[[], None] | None = None,
        before_unit_lock: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(session)
        self._before_building_lock = before_building_lock
        self._before_unit_lock = before_unit_lock

    def get_building_for_update(self, **values):
        callback = self._before_building_lock
        self._before_building_lock = None
        if callback is not None:
            callback()
        return super().get_building_for_update(**values)

    def get_unit_for_update(self, **values):
        callback = self._before_unit_lock
        self._before_unit_lock = None
        if callback is not None:
            callback()
        return super().get_unit_for_update(**values)


def _default(value: object) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    raise TypeError(f"Unsupported JSON value: {type(value).__name__}")


def _audit_details(connection, request_id: str) -> dict[str, object]:
    raw = connection.execute(
        text("SELECT details_json FROM audit_logs WHERE request_id=:request_id"),
        {"request_id": request_id},
    ).scalar_one()
    if isinstance(raw, str):
        return json.loads(raw)
    return dict(raw)


def run() -> dict[str, object]:
    code_sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=REPO, text=True
    ).strip()
    run_id = uuid4().hex[:10]
    prefix = f"phr006-fix2-{run_id}"
    settings = build_isolated_settings(enable_enqueue=True)
    target = make_url(settings.test_database_url)
    if (
        target.host != "127.0.0.1"
        or target.port != 3307
        or target.database != "property_ai_auth001_test"
    ):
        raise RuntimeError("AUTH001 target guard rejected the database")
    engine = create_engine(settings.test_database_url)
    with engine.connect() as connection:
        revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        if revision != "20260919_0009":
            raise RuntimeError(f"Expected 0009, found {revision}")
        manager = _manager_target(connection)

    with httpx.Client(timeout=20.0) as client:
        if client.get(f"{API}/healthz").status_code != 200:
            raise RuntimeError("8083 health check failed")
        manager_session = _sms_login(
            client,
            phone=str(manager["phone_e164"]),
            request_prefix=f"{prefix}-manager",
        )
        manager_token = str(manager_session["access_token"])
        tenant_id = int(manager["tenant_id"])
        property_id = int(manager["property_id"])
        building = _request(
            client,
            "POST",
            "/admin/buildings",
            token=manager_token,
            request_id=f"{prefix}-create-building",
            json_body={
                "tenant_id": tenant_id,
                "property_id": property_id,
                "code": f"CX{run_id.upper()}",
                "name": "并发楼栋原名",
            },
        )
        unit = _request(
            client,
            "POST",
            "/admin/units",
            token=manager_token,
            request_id=f"{prefix}-create-unit",
            json_body={
                "tenant_id": tenant_id,
                "property_id": property_id,
                "building_id": building["id"],
                "code": "U1",
                "name": "并发单元原名",
            },
        )

    with Session(engine) as actor_session:
        actor = AuthService(
            actor_session, IdentityRepository(actor_session)
        ).load_principal(int(manager["user_id"]))

    building_code_by_b = f"CY{run_id.upper()}"
    building_name_by_a = "并发楼栋A名称"
    building_request_a = f"{prefix}-building-a"
    building_request_b = f"{prefix}-building-b"

    def update_building_b() -> None:
        with Session(engine, expire_on_commit=False) as session_b:
            service_b = PropertyAdminService(session_b)
            service_b.update_building(
                actor=actor,
                building_id=int(building["id"]),
                code=building_code_by_b,
                request_id=building_request_b,
            )

    with Session(engine, expire_on_commit=False) as session_a:
        repository_a = InterleavingPropertyRepository(
            session_a, before_building_lock=update_building_b
        )
        building_result_a = PropertyAdminService(
            session_a, repository=repository_a
        ).update_building(
            actor=actor,
            building_id=int(building["id"]),
            name=building_name_by_a,
            request_id=building_request_a,
        )
        building_return = {
            "code": building_result_a.code,
            "name": building_result_a.name,
        }

    unit_code_by_b = f"U{run_id.upper()}"
    unit_name_by_a = "并发单元A名称"
    unit_request_a = f"{prefix}-unit-a"
    unit_request_b = f"{prefix}-unit-b"

    def update_unit_b() -> None:
        with Session(engine, expire_on_commit=False) as session_b:
            service_b = PropertyAdminService(session_b)
            service_b.update_unit(
                actor=actor,
                unit_id=int(unit["id"]),
                code=unit_code_by_b,
                request_id=unit_request_b,
            )

    with Session(engine, expire_on_commit=False) as session_a:
        repository_a = InterleavingPropertyRepository(
            session_a, before_unit_lock=update_unit_b
        )
        unit_result_a = PropertyAdminService(
            session_a, repository=repository_a
        ).update_unit(
            actor=actor,
            unit_id=int(unit["id"]),
            name=unit_name_by_a,
            request_id=unit_request_a,
        )
        unit_return = {"code": unit_result_a.code, "name": unit_result_a.name}

    with engine.connect() as connection:
        building_final = dict(
            connection.execute(
                text("SELECT code,name FROM buildings WHERE id=:id"),
                {"id": int(building["id"])},
            ).mappings().one()
        )
        unit_final = dict(
            connection.execute(
                text("SELECT code,name FROM units WHERE id=:id"),
                {"id": int(unit["id"])},
            ).mappings().one()
        )
        building_audit = _audit_details(connection, building_request_a)
        unit_audit = _audit_details(connection, unit_request_a)

    expected_building = {"code": building_code_by_b, "name": building_name_by_a}
    expected_unit = {"code": unit_code_by_b, "name": unit_name_by_a}
    assertions = {
        "migration_is_0009": revision == "20260919_0009",
        "manager_official_sms_session": bool(manager_session.get("access_token")),
        "building_a_return_refreshed": building_return == expected_building,
        "building_final_combines_b_code_and_a_name": building_final == expected_building,
        "building_a_audit_matches_final": {
            "code": building_audit["code"],
            "name": building_audit["name"],
        }
        == expected_building,
        "unit_a_return_refreshed": unit_return == expected_unit,
        "unit_final_combines_b_code_and_a_name": unit_final == expected_unit,
        "unit_a_audit_matches_final": {
            "code": unit_audit["code"],
            "name": unit_audit["name"],
        }
        == expected_unit,
    }
    if not all(assertions.values()):
        raise RuntimeError(f"Concurrency assertion failed: {assertions}")
    return {
        "task_id": "PROPERTY-HOUSE-RELATION-006-FIX-2",
        "run_id": run_id,
        "code_sha": code_sha,
        "environment": {
            "api": API,
            "database": "127.0.0.1:3307/property_ai_auth001_test",
            "migration": revision,
            "provider": "development SMS substitute",
        },
        "method": (
            "Each A operation used PropertyAdminService. Its repository callback ran and "
            "committed a separate B PropertyAdminService operation after A Session.get and "
            "immediately before A SELECT FOR UPDATE. No direct SQL business mutation was used."
        ),
        "objects": {
            "tenant_id": tenant_id,
            "property_id": property_id,
            "building_id": int(building["id"]),
            "unit_id": int(unit["id"]),
        },
        "building": {
            "operation_a_return": building_return,
            "final": building_final,
            "operation_a_audit": building_audit,
        },
        "unit": {
            "operation_a_return": unit_return,
            "final": unit_final,
            "operation_a_audit": unit_audit,
        },
        "assertions": assertions,
        "status": "PASS",
        "not_claimed": [
            "general database serializability proof",
            "production environment",
            "Production V1 acceptance",
        ],
    }


if __name__ == "__main__":
    result = run()
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=_default), encoding="utf-8")
    print(json.dumps({"task_id": result["task_id"], "status": result["status"], "assertions": result["assertions"]}, ensure_ascii=False))
