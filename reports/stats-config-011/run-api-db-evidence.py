"""Collect redacted AUTH001 API and read-only DB evidence for STATS-CONFIG-UI-011."""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import re
import site
import subprocess
import sys
from urllib.parse import urlencode, urlsplit
from uuid import uuid4


REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"
OPS = REPO / "scripts" / "ops"
for candidate in (BACKEND, OPS):
    sys.path.insert(0, str(candidate))
site.addsitedir(str(REPO / ".venv" / "Lib" / "site-packages"))

import httpx  # noqa: E402
from sqlalchemy import bindparam, create_engine, text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402

from auth001_isolated_runtime import (  # noqa: E402
    ISOLATED_QUEUE,
    ISOLATED_VHOST,
    build_isolated_settings,
)


TASK_ID = "STATS-CONFIG-UI-011"
API = "http://127.0.0.1:8083/api/v1"
SOURCE_CONTEXT = REPO / "tmp" / "sla-policy-management-010" / "context.json"
PRIVATE_DIR = REPO / "tmp" / "stats-config-011"
PRIVATE_CONTEXT = PRIVATE_DIR / "context.json"
PUBLIC_EVIDENCE = Path(__file__).resolve().parent / "api-database-evidence.json"
WORK_ORDER_STATUSES = (
    "PENDING_ANALYSIS",
    "PENDING_ASSIGNMENT",
    "ASSIGNED",
    "ACCEPTED",
    "PROCESSING",
    "WAIT_CONFIRM",
    "COMPLETED",
    "CANCELLED",
    "CLOSED",
)
WORK_ORDER_CATEGORIES = (
    "PLUMBING",
    "ELECTRICAL",
    "HVAC",
    "CLEANING",
    "SECURITY",
    "OTHER",
)
WORK_ORDER_PRIORITIES = ("LOW", "MEDIUM", "HIGH", "URGENT")
INCIDENT_STATUSES = ("OPEN", "MONITORING", "RESOLVED", "CLOSED")
SLA_STATES = ("OPEN", "WARNING", "OVERDUE", "STOPPED")
TERMINAL_STATUSES = {"COMPLETED", "CANCELLED", "CLOSED"}
ACTIVE_INCIDENT_STATUSES = {"OPEN", "MONITORING"}


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _challenge_answer(prompt: str) -> str:
    match = re.fullmatch(r"\s*(\d+)\s*\+\s*(\d+)\s*=\s*\?\s*", prompt)
    if match is None:
        raise RuntimeError("Unsupported Development challenge format")
    return str(int(match.group(1)) + int(match.group(2)))


def _error_code(payload: dict[str, object]) -> str | None:
    error = payload.get("error")
    return str(error.get("code")) if isinstance(error, dict) else None


class Client:
    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self.http = httpx.Client(base_url=API, timeout=30.0)
        self.request_ids: dict[str, str] = {}

    def close(self) -> None:
        self.http.close()

    def call(
        self,
        method: str,
        path: str,
        *,
        label: str,
        token: str | None = None,
        body: dict[str, object] | None = None,
        expected: tuple[int, ...] = (200,),
    ) -> tuple[int, dict[str, object]]:
        request_id = f"{self.run_id}-{label}"
        if label in self.request_ids:
            raise RuntimeError(f"Duplicate request label: {label}")
        self.request_ids[label] = request_id
        headers = {"X-Request-Id": request_id}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        response = self.http.request(method, path, headers=headers, json=body)
        try:
            payload = response.json()
        except ValueError as exc:
            raise RuntimeError(
                f"{label} returned non-JSON HTTP {response.status_code}"
            ) from exc
        if response.status_code not in expected:
            raise RuntimeError(
                f"{label} failed: HTTP {response.status_code} "
                f"code={_error_code(payload)}"
            )
        return response.status_code, payload

    def data(self, *args, **kwargs) -> dict[str, object]:
        _status, payload = self.call(*args, **kwargs)
        data = payload.get("data")
        if not isinstance(data, dict):
            raise RuntimeError(
                f"{kwargs.get('label', 'request')} returned no data object"
            )
        return data

    def sms_login(self, phone: str, label: str) -> dict[str, object]:
        challenge = self.data(
            "POST",
            "/auth/challenge",
            body={"phone": phone, "purpose": "login"},
            label=f"{label}-challenge",
        )
        self.data(
            "POST",
            "/auth/sms/send",
            body={
                "phone": phone,
                "purpose": "login",
                "challenge_id": challenge["challenge_id"],
                "challenge_answer": _challenge_answer(str(challenge["prompt"])),
            },
            label=f"{label}-sms-send",
        )
        inbox = self.data(
            "POST",
            "/auth/sms/test-inbox",
            body={"phone": phone, "purpose": "login"},
            label=f"{label}-sms-inbox",
        )
        return self.data(
            "POST",
            "/auth/sms/login",
            body={"phone": phone, "code": inbox["code"]},
            label=f"{label}-sms-login",
        )


def _guard(settings) -> None:
    database = make_url(settings.test_database_url)
    broker = urlsplit(settings.celery_broker_url)
    actual = (
        database.host,
        database.port,
        database.database,
        settings.app_env,
        settings.celery_task_queue,
        broker.path.lstrip("/"),
    )
    expected = (
        "127.0.0.1",
        3307,
        "property_ai_auth001_test",
        "test",
        ISOLATED_QUEUE,
        ISOLATED_VHOST,
    )
    if actual != expected:
        raise RuntimeError(f"AUTH001 guard mismatch: {actual!r}")


def _scope_has(
    me: dict[str, object],
    *,
    permission: str,
    scope_type: str,
    tenant_id: int,
    property_id: int | None,
) -> bool:
    return any(
        isinstance(scope, dict)
        and scope.get("scope_type") == scope_type
        and int(scope.get("tenant_id") or 0) == tenant_id
        and scope.get("property_id") == property_id
        and permission in (scope.get("permissions") or [])
        for scope in (me.get("scopes") or [])
    )


def _api_counts(items: object) -> dict[str, int]:
    if not isinstance(items, list):
        raise RuntimeError("Statistics response count list is invalid")
    return {str(item["code"]): int(item["count"]) for item in items}


def _db_counts(connection, sql: str, values: dict[str, object]) -> dict[str, int]:
    return {
        str(row[0]): int(row[1])
        for row in connection.execute(text(sql), values).all()
    }


def _zero_fill(codes: tuple[str, ...], counts: dict[str, int]) -> dict[str, int]:
    return {code: int(counts.get(code, 0)) for code in codes}


def _db_snapshot(
    settings,
    *,
    tenant_id: int,
    property_id: int,
    generated_at: datetime,
    business_date: str,
    stats_request_ids: list[str],
) -> dict[str, object]:
    engine = create_engine(settings.test_database_url)
    at_utc = generated_at.astimezone(UTC).replace(tzinfo=None)
    values = {"tenant_id": tenant_id, "property_id": property_id}
    with engine.connect() as connection:
        connection.exec_driver_sql("SET TRANSACTION READ ONLY")
        revision = connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one()
        property_row = connection.execute(
            text(
                "SELECT name, timezone FROM properties "
                "WHERE tenant_id=:tenant_id AND id=:property_id"
            ),
            values,
        ).one()
        property_count = connection.execute(
            text("SELECT COUNT(*) FROM properties WHERE tenant_id=:tenant_id"),
            {"tenant_id": tenant_id},
        ).scalar_one()
        work_status = _db_counts(
            connection,
            "SELECT status, COUNT(*) FROM work_orders "
            "WHERE tenant_id=:tenant_id AND property_id=:property_id GROUP BY status",
            values,
        )
        work_category = _db_counts(
            connection,
            "SELECT category, COUNT(*) FROM work_orders "
            "WHERE tenant_id=:tenant_id AND property_id=:property_id GROUP BY category",
            values,
        )
        work_priority = _db_counts(
            connection,
            "SELECT priority, COUNT(*) FROM work_orders "
            "WHERE tenant_id=:tenant_id AND property_id=:property_id GROUP BY priority",
            values,
        )
        incidents = _db_counts(
            connection,
            "SELECT status, COUNT(*) FROM incidents "
            "WHERE tenant_id=:tenant_id AND property_id=:property_id GROUP BY status",
            values,
        )
        sla_states = _db_counts(
            connection,
            "SELECT sc.state, COUNT(*) FROM sla_clocks sc "
            "JOIN work_orders wo ON wo.id=sc.work_order_id "
            "AND wo.tenant_id=sc.tenant_id AND wo.property_id=sc.property_id "
            "WHERE wo.tenant_id=:tenant_id AND wo.property_id=:property_id "
            "GROUP BY sc.state",
            values,
        )
        active_assignments = connection.execute(
            text(
                "SELECT COUNT(DISTINCT ep.employee_id) FROM employee_properties ep "
                "JOIN employees e ON e.id=ep.employee_id AND e.tenant_id=ep.tenant_id "
                "WHERE ep.tenant_id=:tenant_id AND ep.property_id=:property_id "
                "AND ep.status='ACTIVE' AND ep.valid_from<=:at "
                "AND (ep.valid_to IS NULL OR ep.valid_to>:at) AND e.status='ACTIVE'"
            ),
            {**values, "at": at_utc},
        ).scalar_one()
        staffing = connection.execute(
            text(
                "SELECT COUNT(eds.id), "
                "COALESCE(SUM(CASE WHEN eds.on_duty='ON_DUTY' THEN 1 ELSE 0 END),0), "
                "COALESCE(SUM(CASE WHEN eds.on_duty='ON_DUTY' "
                "AND eds.current_load<e.max_capacity THEN 1 ELSE 0 END),0), "
                "COALESCE(SUM(CASE WHEN eds.on_duty='ON_DUTY' "
                "THEN eds.current_load ELSE 0 END),0), "
                "COALESCE(SUM(CASE WHEN eds.on_duty='ON_DUTY' "
                "THEN e.max_capacity ELSE 0 END),0) "
                "FROM employee_daily_statuses eds "
                "JOIN employees e ON e.id=eds.employee_id AND e.tenant_id=eds.tenant_id "
                "JOIN employee_properties ep ON ep.employee_id=eds.employee_id "
                "AND ep.tenant_id=eds.tenant_id AND ep.property_id=eds.property_id "
                "WHERE eds.tenant_id=:tenant_id AND eds.property_id=:property_id "
                "AND eds.business_date=:business_date AND e.status='ACTIVE' "
                "AND ep.status='ACTIVE' AND ep.valid_from<=:at "
                "AND (ep.valid_to IS NULL OR ep.valid_to>:at)"
            ),
            {**values, "at": at_utc, "business_date": business_date},
        ).one()
        role_codes = connection.execute(
            text(
                "SELECT r.code FROM roles r "
                "JOIN role_permissions rp ON rp.role_id=r.id "
                "JOIN permissions p ON p.id=rp.permission_id "
                "WHERE p.code='stats:read' ORDER BY r.code"
            )
        ).scalars().all()
        stats_audits = connection.execute(
            text(
                "SELECT COUNT(*) FROM audit_logs "
                "WHERE request_id IN :request_ids"
            ).bindparams(bindparam("request_ids", expanding=True)),
            {"request_ids": stats_request_ids},
        ).scalar_one()
    engine.dispose()
    filled_status = _zero_fill(WORK_ORDER_STATUSES, work_status)
    filled_incidents = _zero_fill(INCIDENT_STATUSES, incidents)
    filled_sla = _zero_fill(SLA_STATES, sla_states)
    terminal = sum(filled_status[code] for code in TERMINAL_STATUSES)
    total = sum(filled_status.values())
    return {
        "revision": revision,
        "property_count": int(property_count),
        "property_name": str(property_row[0]),
        "timezone": str(property_row[1]),
        "work_orders": {
            "total": total,
            "open": total - terminal,
            "terminal": terminal,
            "by_status": filled_status,
            "by_category": _zero_fill(WORK_ORDER_CATEGORIES, work_category),
            "by_priority": _zero_fill(WORK_ORDER_PRIORITIES, work_priority),
        },
        "incidents": {
            "total": sum(filled_incidents.values()),
            "active": sum(filled_incidents[code] for code in ACTIVE_INCIDENT_STATUSES),
            "by_status": filled_incidents,
        },
        "sla_clocks": {
            "warning": filled_sla["WARNING"],
            "overdue": filled_sla["OVERDUE"],
            "by_state": filled_sla,
        },
        "staffing": {
            "active_property_assignments": int(active_assignments),
            "current_status_records": int(staffing[0]),
            "unreported_status": max(int(active_assignments) - int(staffing[0]), 0),
            "on_duty": int(staffing[1]),
            "available": int(staffing[2]),
            "current_load": int(staffing[3]),
            "on_duty_capacity": int(staffing[4]),
        },
        "stats_request_audit_rows": int(stats_audits),
        "stats_read_role_codes": list(role_codes),
    }


def _assert_matches(
    overview: dict[str, object],
    snapshot: dict[str, object],
    *,
    tenant_id: int,
    property_id: int | None,
) -> None:
    expected_scope = "TENANT" if property_id is None else "PROPERTY"
    if overview.get("scope_type") != expected_scope:
        raise RuntimeError("Overview scope type mismatch")
    if int(overview.get("tenant_id") or 0) != tenant_id:
        raise RuntimeError("Overview tenant mismatch")
    if overview.get("property_id") != property_id:
        raise RuntimeError("Overview property mismatch")
    if int(overview.get("property_count") or 0) != snapshot["property_count"]:
        raise RuntimeError("Overview property count mismatch")

    work = overview.get("work_orders") or {}
    db_work = snapshot["work_orders"]
    if {
        "total": int(work.get("total") or 0),
        "open": int(work.get("open") or 0),
        "terminal": int(work.get("terminal") or 0),
        "by_status": _api_counts(work.get("by_status")),
        "by_category": _api_counts(work.get("by_category")),
        "by_priority": _api_counts(work.get("by_priority")),
    } != db_work:
        raise RuntimeError("Work-order aggregate does not match the database")

    incidents = overview.get("incidents") or {}
    db_incidents = snapshot["incidents"]
    if {
        "total": int(incidents.get("total") or 0),
        "active": int(incidents.get("active") or 0),
        "by_status": _api_counts(incidents.get("by_status")),
    } != db_incidents:
        raise RuntimeError("Incident aggregate does not match the database")

    sla = overview.get("sla_clocks") or {}
    db_sla = snapshot["sla_clocks"]
    if {
        "warning": int(sla.get("warning") or 0),
        "overdue": int(sla.get("overdue") or 0),
        "by_state": _api_counts(sla.get("by_state")),
    } != db_sla:
        raise RuntimeError("SLA aggregate does not match the database")
    if overview.get("staffing") != snapshot["staffing"]:
        raise RuntimeError("Staffing aggregate does not match the database")

    properties = overview.get("properties") or []
    if len(properties) != 1:
        raise RuntimeError("Expected exactly one current AUTH001 property")
    row = properties[0]
    expected_property_id = property_id or int(row.get("property_id") or 0)
    if int(row.get("property_id") or 0) != expected_property_id:
        raise RuntimeError("Property breakdown id mismatch")
    if (
        row.get("property_name") != snapshot["property_name"]
        or row.get("timezone") != snapshot["timezone"]
    ):
        raise RuntimeError("Property breakdown metadata mismatch")


def main() -> int:
    if not SOURCE_CONTEXT.exists():
        raise RuntimeError("Existing normal SMS synthetic context is missing")
    source = json.loads(SOURCE_CONTEXT.read_text(encoding="utf-8"))
    phones = source.get("phones") or {}
    for role in ("manager", "property_admin", "primary", "staff"):
        if not phones.get(role):
            raise RuntimeError(f"Missing normal SMS identity: {role}")
    tenant_id = int(source["org"]["tenant_id"])
    property_id = int(source["org"]["property_id"])
    settings = build_isolated_settings(enable_enqueue=False)
    _guard(settings)
    run_id = f"stats-config-api-{uuid4().hex[:12]}"
    client = Client(run_id)
    try:
        sessions = {
            role: client.sms_login(str(phones[role]), role)
            for role in ("manager", "property_admin", "primary", "staff")
        }
        me = {
            role: client.data(
                "GET",
                "/me",
                token=str(session["access_token"]),
                label=f"{role}-me",
            )
            for role, session in sessions.items()
        }
        if not _scope_has(
            me["manager"],
            permission="stats:read",
            scope_type="TENANT",
            tenant_id=tenant_id,
            property_id=None,
        ):
            raise RuntimeError("Tenant Admin normal session lacks tenant stats:read")
        if not _scope_has(
            me["property_admin"],
            permission="stats:read",
            scope_type="PROPERTY",
            tenant_id=tenant_id,
            property_id=property_id,
        ):
            raise RuntimeError("Property Admin normal session lacks property stats:read")
        if any("stats:read" in (scope.get("permissions") or []) for role in ("primary", "staff") for scope in (me[role].get("scopes") or [])):
            raise RuntimeError("Resident or Staff unexpectedly received stats:read")

        property_query = urlencode(
            {
                "scope_type": "PROPERTY",
                "tenant_id": tenant_id,
                "property_id": property_id,
            }
        )
        tenant_query = urlencode(
            {"scope_type": "TENANT", "tenant_id": tenant_id}
        )
        property_overview = client.data(
            "GET",
            f"/admin/operations-overview?{property_query}",
            token=str(sessions["property_admin"]["access_token"]),
            label="property-admin-overview",
        )
        tenant_overview = client.data(
            "GET",
            f"/admin/operations-overview?{tenant_query}",
            token=str(sessions["manager"]["access_token"]),
            label="tenant-admin-overview",
        )
        manager_property_overview = client.data(
            "GET",
            f"/admin/operations-overview?{property_query}",
            token=str(sessions["manager"]["access_token"]),
            label="tenant-admin-property-overview",
        )

        negative: dict[str, dict[str, object]] = {}
        for label, token, path, expected in (
            (
                "property-admin-tenant-denied",
                sessions["property_admin"]["access_token"],
                f"/admin/operations-overview?{tenant_query}",
                (403,),
            ),
            (
                "resident-denied",
                sessions["primary"]["access_token"],
                f"/admin/operations-overview?{property_query}",
                (403,),
            ),
            (
                "staff-denied",
                sessions["staff"]["access_token"],
                f"/admin/operations-overview?{property_query}",
                (403,),
            ),
            (
                "cross-tenant-denied",
                sessions["manager"]["access_token"],
                "/admin/operations-overview?scope_type=PROPERTY"
                f"&tenant_id={tenant_id + 999999}&property_id={property_id}",
                (403,),
            ),
            (
                "tenant-property-shape-rejected",
                sessions["manager"]["access_token"],
                f"/admin/operations-overview?scope_type=TENANT"
                f"&tenant_id={tenant_id}&property_id={property_id}",
                (422,),
            ),
        ):
            status, payload = client.call(
                "GET",
                path,
                token=str(token),
                label=label,
                expected=expected,
            )
            negative[label] = {
                "status": status,
                "error_code": _error_code(payload),
            }

        generated_at = datetime.fromisoformat(
            str(property_overview["generated_at"]).replace("Z", "+00:00")
        )
        property_row = (property_overview.get("properties") or [])[0]
        stats_labels = [
            "property-admin-overview",
            "tenant-admin-overview",
            "tenant-admin-property-overview",
            *negative.keys(),
        ]
        snapshot = _db_snapshot(
            settings,
            tenant_id=tenant_id,
            property_id=property_id,
            generated_at=generated_at,
            business_date=str(property_row["business_date"]),
            stats_request_ids=[client.request_ids[label] for label in stats_labels],
        )
        _assert_matches(
            property_overview,
            snapshot,
            tenant_id=tenant_id,
            property_id=property_id,
        )
        _assert_matches(
            manager_property_overview,
            snapshot,
            tenant_id=tenant_id,
            property_id=property_id,
        )
        _assert_matches(
            tenant_overview,
            snapshot,
            tenant_id=tenant_id,
            property_id=None,
        )
        if snapshot["revision"] != "20260919_0009":
            raise RuntimeError("AUTH001 revision changed")
        if snapshot["stats_read_role_codes"] != [
            "PROPERTY_ADMIN",
            "TENANT_ADMIN",
        ]:
            raise RuntimeError("Runtime stats permission mapping is not exact")
        if snapshot["stats_request_audit_rows"] != 0:
            raise RuntimeError("Read-only statistics requests wrote audit rows")

        code_sha = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=REPO, text=True
        ).strip()
        PRIVATE_DIR.mkdir(parents=True, exist_ok=True)
        PRIVATE_CONTEXT.write_text(
            json.dumps(
                {
                    "task_id": TASK_ID,
                    "run_id": run_id,
                    "org": {"tenant_id": tenant_id, "property_id": property_id},
                    "phones": {
                        role: str(phones[role])
                        for role in ("manager", "property_admin", "primary", "staff")
                    },
                    "identities": {
                        role: int(session["user_id"])
                        for role, session in sessions.items()
                    },
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        public = {
            "task_id": TASK_ID,
            "phase": "api_and_database_read_only",
            "captured_at": datetime.now(UTC).isoformat(),
            "code_sha": code_sha,
            "run_id": run_id,
            "environment": {
                "api": API,
                "database": "127.0.0.1:3307/property_ai_auth001_test",
                "revision": snapshot["revision"],
                "rabbit_vhost": ISOLATED_VHOST,
                "rabbit_queue": ISOLATED_QUEUE,
                "provider": "DEVELOPMENT_SMS_AND_FAKE_AI",
            },
            "objects": {"tenant_id": tenant_id, "property_id": property_id},
            "identities": {
                role: int(session["user_id"])
                for role, session in sessions.items()
            },
            "checks": {
                "normal_development_sms_sessions": True,
                "seed_or_dev_token_login_used": False,
                "runtime_permission_roles_exact": snapshot["stats_read_role_codes"],
                "tenant_admin_tenant_and_property_overviews": True,
                "property_admin_property_overview": True,
                "resident_and_staff_stats_permission_absent": True,
                "api_matches_same_scope_read_only_database": True,
                "read_requests_created_no_audit": True,
                "negative_paths": negative,
            },
            "snapshot": {
                "generated_at": property_overview["generated_at"],
                "business_date": property_row["business_date"],
                "property_count": snapshot["property_count"],
                "work_orders": snapshot["work_orders"],
                "incidents": snapshot["incidents"],
                "sla_clocks": snapshot["sla_clocks"],
                "staffing": snapshot["staffing"],
            },
            "request_id_hashes": {
                label: _hash(request_id)
                for label, request_id in client.request_ids.items()
            },
            "boundaries": {
                "database_queries_read_only": True,
                "direct_database_business_write": False,
                "migration_run": False,
                "shared_vhost_read_consumed_replayed_or_cleared": False,
                "development_ai_claimed_as_real": False,
                "ui_claimed": False,
            },
            "private_context": "tmp/stats-config-011/context.json",
            "redaction": (
                "No phone, OTP, token, password, resident text, employee identity, "
                "or full request ID is emitted."
            ),
        }
        PUBLIC_EVIDENCE.write_text(
            json.dumps(public, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(
            json.dumps(
                {
                    "task_id": TASK_ID,
                    "code_sha": code_sha,
                    "run_id": run_id,
                    "normal_sessions": list(public["identities"].keys()),
                    "negative_paths": negative,
                    "snapshot": public["snapshot"],
                    "evidence": str(PUBLIC_EVIDENCE.relative_to(REPO)),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
