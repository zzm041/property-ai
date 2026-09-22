"""Prepare bounded AUTH001 business objects for RESIDENT-WO-PAGING-ACTIONS-012.

The script has two explicit phases.  ``rating`` runs with the isolated Worker
online and closes two freshly created orders through authenticated business
APIs.  ``pending`` runs only after the operator has stopped that exact Worker;
it creates the stable pending orders used by the page actions and pagination
checks.  Secrets and phone numbers are written only to the private tmp context.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import re
import secrets
import site
import subprocess
import sys
import time
from urllib.parse import urlsplit


REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"
OPS = REPO / "scripts" / "ops"
for candidate in (BACKEND, OPS):
    sys.path.insert(0, str(candidate))
site.addsitedir(str(REPO / ".venv" / "Lib" / "site-packages"))

import httpx  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402

from auth001_isolated_runtime import (  # noqa: E402
    ISOLATED_QUEUE,
    ISOLATED_VHOST,
    build_isolated_settings,
)


TASK_ID = "RESIDENT-WO-PAGING-ACTIONS-012"
API = "http://127.0.0.1:8083/api/v1"
SOURCE = REPO / "tmp" / "sla-policy-management-010" / "context.json"
PRIVATE_DIR = REPO / "tmp" / "resident-wo-paging-actions-012"
PRIVATE_CONTEXT = PRIVATE_DIR / "context.json"
PUBLIC_EVIDENCE = Path(__file__).with_name("preparation-evidence.json")
TARGET_TOTAL = 22


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
        self.requests: list[dict[str, object]] = []

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
        params: dict[str, object] | None = None,
        headers: dict[str, str] | None = None,
        expected: tuple[int, ...] = (200,),
    ) -> tuple[int, dict[str, object]]:
        request_id = f"{self.run_id}-{label}"
        merged_headers = {"X-Request-Id": request_id, **(headers or {})}
        if token:
            merged_headers["Authorization"] = f"Bearer {token}"
        response = self.http.request(
            method,
            path,
            headers=merged_headers,
            json=body,
            params=params,
        )
        try:
            payload = response.json()
        except ValueError as exc:
            raise RuntimeError(f"{label} returned non-JSON HTTP {response.status_code}") from exc
        self.requests.append(
            {
                "label": label,
                "method": method,
                "path": path,
                "status": response.status_code,
                "error_code": _error_code(payload),
                "request_id_hash": _hash(request_id),
            }
        )
        if response.status_code not in expected:
            raise RuntimeError(
                f"{label} failed: HTTP {response.status_code} code={_error_code(payload)}"
            )
        return response.status_code, payload

    def data(self, *args, **kwargs) -> dict[str, object]:
        _status, payload = self.call(*args, **kwargs)
        data = payload.get("data")
        if not isinstance(data, dict):
            raise RuntimeError(f"{kwargs.get('label', 'request')} returned no data object")
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


def _guard() -> object:
    settings = build_isolated_settings()
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
    return settings


def _interrupted_rating_object(settings) -> dict[str, object] | None:
    """Recover the one object created by an interrupted first preparation."""

    engine = create_engine(settings.test_database_url)
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql("SET TRANSACTION READ ONLY")
            rows = [
                dict(row)
                for row in connection.execute(
                    text(
                        "SELECT wo.id, wo.status, woc.text AS marker "
                        "FROM work_orders wo "
                        "JOIN work_order_contents woc ON woc.work_order_id=wo.id "
                        "WHERE woc.text LIKE :marker_pattern "
                        "ORDER BY wo.id"
                    ),
                    {"marker_pattern": "wo012-%:rating_page"},
                ).mappings()
            ]
    finally:
        engine.dispose()
    if not rows:
        return None
    if len(rows) != 1:
        raise RuntimeError("Multiple untracked WO-012 rating objects require manual inspection")
    marker = str(rows[0]["marker"])
    rows[0]["run_id"] = marker.split(":", 1)[0]
    return rows[0]


def _run_rows(settings, run_id: str) -> list[dict[str, object]]:
    engine = create_engine(settings.test_database_url)
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql("SET TRANSACTION READ ONLY")
            return [
                dict(row)
                for row in connection.execute(
                    text(
                        "SELECT wo.id, wo.status, woc.text AS marker "
                        "FROM work_orders wo "
                        "JOIN work_order_contents woc ON woc.work_order_id=wo.id "
                        "WHERE woc.text LIKE :marker_pattern ORDER BY wo.id"
                    ),
                    {"marker_pattern": f"{run_id}:%"},
                ).mappings()
            ]
    finally:
        engine.dispose()


def _recovered_capacity_release_id(settings, run_id: str) -> int | None:
    engine = create_engine(settings.test_database_url)
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql("SET TRANSACTION READ ONLY")
            value = connection.execute(
                text(
                    "SELECT work_order_id FROM work_order_logs "
                    "WHERE request_id=:request_id ORDER BY id DESC LIMIT 1"
                ),
                {"request_id": f"{run_id}-capacity_release-confirm"},
            ).scalar_one_or_none()
            return None if value is None else int(value)
    finally:
        engine.dispose()


def _save_private(context: dict[str, object]) -> None:
    PRIVATE_DIR.mkdir(parents=True, exist_ok=True)
    PRIVATE_CONTEXT.write_text(
        json.dumps(context, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _save_public(context: dict[str, object], client: Client) -> None:
    phases = context.get("phases") or {}
    public = {
        "task_id": TASK_ID,
        "captured_at": datetime.now(UTC).isoformat(),
        "code_sha": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=REPO, text=True
        ).strip(),
        "run_id": context["run_id"],
        "environment": {
            "api": API,
            "database": "127.0.0.1:3307/property_ai_auth001_test",
            "revision": "20260919_0009",
            "rabbit_vhost": ISOLATED_VHOST,
            "rabbit_queue": ISOLATED_QUEUE,
            "providers": "DEVELOPMENT_SMS_AND_FAKE_AI",
        },
        "identities": context.get("identities"),
        "objects": context.get("objects"),
        "phases": phases,
        "requests": client.requests,
        "boundaries": {
            "normal_sms_login": True,
            "seed_or_dev_token_login": False,
            "direct_database_business_write": False,
            "migration_run": False,
            "shared_vhost_touched": False,
            "real_model_claimed": False,
            "production_claimed": False,
            "phones_tokens_and_otp_persisted_publicly": False,
        },
    }
    PUBLIC_EVIDENCE.write_text(
        json.dumps(public, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _work_order(client: Client, token: str, work_order_id: int, label: str) -> dict[str, object]:
    return client.data(
        "GET",
        f"/work-orders/{work_order_id}",
        token=token,
        label=label,
    )


def _wait_assigned(
    client: Client,
    token: str,
    work_order_id: int,
    label: str,
) -> dict[str, object]:
    last: dict[str, object] = {}
    for attempt in range(80):
        last = _work_order(client, token, work_order_id, f"{label}-poll-{attempt}")
        work_order = last.get("work_order") or {}
        if work_order.get("status") == "ASSIGNED" and last.get("assignments"):
            return last
        if work_order.get("status") not in {"PENDING_ANALYSIS", "PENDING_ASSIGNMENT"}:
            raise RuntimeError(
                f"{label} reached unexpected status {work_order.get('status')}"
            )
        time.sleep(0.25)
    raise RuntimeError(
        f"{label} did not become ASSIGNED; last={((last.get('work_order') or {}).get('status'))}"
    )


def _create(
    client: Client,
    *,
    token: str,
    house_id: int,
    marker: str,
    label: str,
) -> dict[str, object]:
    return client.data(
        "POST",
        "/work-orders",
        token=token,
        headers={"Idempotency-Key": f"{client.run_id}-{label}"},
        body={
            "house_id": house_id,
            "text": marker,
        },
        label=label,
    )


def _close_for_rating(
    client: Client,
    *,
    resident_token: str,
    staff_token: str,
    work_order_id: int,
    staff_employee_id: int,
    label: str,
) -> dict[str, object]:
    detail = _wait_assigned(client, resident_token, work_order_id, label)
    assignment = (detail.get("assignments") or [])[-1]
    if int(assignment["employee_id"]) != staff_employee_id:
        raise RuntimeError(f"{label} was assigned to an unexpected synthetic employee")
    version = int((detail["work_order"])["version"])
    accepted = client.data(
        "POST",
        f"/staff/work-orders/{work_order_id}/accept",
        token=staff_token,
        body={"version": version},
        label=f"{label}-accept",
    )
    started = client.data(
        "POST",
        f"/staff/work-orders/{work_order_id}/start",
        token=staff_token,
        body={"version": int(accepted["version"])},
        label=f"{label}-start",
    )
    receipted = client.data(
        "POST",
        f"/staff/work-orders/{work_order_id}/receipts",
        token=staff_token,
        body={
            "problem_type": "ELECTRICAL",
            "method": f"Synthetic scoped verification {client.run_id}",
            "materials": None,
            "fee_flag": "NONE",
            "resolved": True,
            "before_file_ids": [],
            "after_file_ids": [],
            "version": int(started["version"]),
        },
        label=f"{label}-receipt",
    )
    closed = client.data(
        "POST",
        f"/work-orders/{work_order_id}/confirm",
        token=resident_token,
        body={"version": int(receipted["version"])},
        label=f"{label}-confirm",
    )
    if closed["status"] != "CLOSED":
        raise RuntimeError(f"{label} did not close")
    return closed


def _rating_phase() -> None:
    if PRIVATE_CONTEXT.exists():
        raise RuntimeError("Private WO-012 context already exists; refusing duplicate preparation")
    settings = _guard()
    interrupted = _interrupted_rating_object(settings)
    source = json.loads(SOURCE.read_text(encoding="utf-8"))
    run_id = (
        str(interrupted["run_id"])
        if interrupted is not None
        else f"wo012-{secrets.token_hex(6)}"
    )
    client = Client(run_id)
    try:
        client.call("GET", "/readyz", label="ready")
        phones = {
            role: str(source["phones"][role])
            for role in ("primary", "secondary", "staff", "manager")
        }
        sessions = {role: client.sms_login(phone, role) for role, phone in phones.items()}
        identities = {role: int(session["user_id"]) for role, session in sessions.items()}
        if len(set(identities.values())) != len(identities):
            raise RuntimeError("Synthetic resident/staff identities are not distinct")
        tokens = {role: str(session["access_token"]) for role, session in sessions.items()}
        org = {
            key: int(source["org"][key])
            for key in ("tenant_id", "property_id", "house_id")
        }
        houses = client.data(
            "GET",
            "/me/houses",
            token=tokens["primary"],
            label="primary-houses",
        ).get("items") or []
        if not any(
            int(item["house_id"]) == org["house_id"] and item["status"] == "ACTIVE"
            for item in houses
        ):
            raise RuntimeError("Primary synthetic resident has no ACTIVE approved house relation")
        attendance = client.data(
            "GET",
            "/staff/attendance/today",
            token=tokens["staff"],
            params={
                "tenant_id": org["tenant_id"],
                "property_id": org["property_id"],
            },
            label="staff-attendance",
        )
        checkin_performed = False
        status_update_performed = False
        if attendance.get("attendance") is None:
            attendance = client.data(
                "POST",
                "/staff/attendance/checkin",
                token=tokens["staff"],
                body={
                    "tenant_id": org["tenant_id"],
                    "property_id": org["property_id"],
                },
                label="staff-checkin",
            )
            checkin_performed = True
        if (
            not isinstance(attendance.get("attendance"), dict)
            or attendance["attendance"].get("check_out_at") is not None
        ):
            raise RuntimeError("Synthetic staff has no active attendance record")
        if (
            not isinstance(attendance.get("daily_status"), dict)
            or attendance["daily_status"].get("on_duty") != "ON_DUTY"
        ):
            attendance = client.data(
                "PATCH",
                "/staff/attendance/status",
                token=tokens["staff"],
                body={
                    "tenant_id": org["tenant_id"],
                    "property_id": org["property_id"],
                    "on_duty": "ON_DUTY",
                },
                label="staff-on-duty",
            )
            status_update_performed = True
        if attendance["daily_status"].get("on_duty") != "ON_DUTY":
            raise RuntimeError("Synthetic staff did not become ON_DUTY")
        employee_id = int(attendance["attendance"]["employee_id"])
        objects: dict[str, object] = {}
        capacity_release_id: int | None = None
        if interrupted is not None:
            interrupted_rows = _run_rows(settings, run_id)
            objects["interrupted_pending_ids"] = [
                int(row["id"]) for row in interrupted_rows
            ]
            objects["interrupted_pending_marker_hashes"] = [
                _hash(str(row["marker"])) for row in interrupted_rows
            ]
            # A prior recovery attempt closed one old assigned synthetic order,
            # but its previous-day capacity snapshot did not lower today's
            # current_load.  Raise the existing synthetic employee capacity via
            # the already-authorized management API; do not edit the database.
            client.data(
                "PATCH",
                f"/admin/employees/{employee_id}",
                token=tokens["manager"],
                body={"max_capacity": 8},
                label="configure-evidence-capacity",
            )
            capacity_release_id = _recovered_capacity_release_id(settings, run_id)
            if capacity_release_id is None:
                raise RuntimeError("Interrupted capacity-release mutation was not found")
            objects["capacity_release_id"] = capacity_release_id
        for role in ("rating_page_final", "rating_race"):
            marker = f"{run_id}:{role}"
            created = _create(
                client,
                token=tokens["primary"],
                house_id=org["house_id"],
                marker=marker,
                label=f"create-{role}",
            )
            work_order_id = int(created["id"])
            _close_for_rating(
                client,
                resident_token=tokens["primary"],
                staff_token=tokens["staff"],
                work_order_id=work_order_id,
                staff_employee_id=employee_id,
                label=role,
            )
            objects[f"{role}_id"] = work_order_id
            objects[f"{role}_marker_hash"] = _hash(marker)
        objects["rating_page_id"] = objects.pop("rating_page_final_id")
        objects["rating_page_marker_hash"] = objects.pop(
            "rating_page_final_marker_hash"
        )
        context = {
            "task_id": TASK_ID,
            "run_id": run_id,
            "phones": phones,
            "identities": identities,
            "org": {**org, "employee_id": employee_id},
            "objects": objects,
            "phases": {
                "rating": {
                    "completed": True,
                    "created_count": 2,
                    "final_status": "CLOSED",
                    "normal_business_api": True,
                    "staff_checkin_performed": checkin_performed,
                    "staff_status_update_performed": status_update_performed,
                    "interrupted_first_attempt_recovered": interrupted is not None,
                    "capacity_release_id": capacity_release_id,
                }
            },
        }
        _save_private(context)
        _save_public(context, client)
    finally:
        client.close()


def _pending_phase() -> None:
    _guard()
    if not PRIVATE_CONTEXT.exists():
        raise RuntimeError("Run rating preparation before pending preparation")
    context = json.loads(PRIVATE_CONTEXT.read_text(encoding="utf-8"))
    if (context.get("phases") or {}).get("pending"):
        raise RuntimeError("Pending preparation already completed; refusing duplicate objects")
    client = Client(str(context["run_id"]))
    try:
        session = client.sms_login(str(context["phones"]["primary"]), "pending-primary")
        token = str(session["access_token"])
        page = client.data(
            "GET",
            "/work-orders",
            token=token,
            params={"after_id": 0, "limit": 100},
            label="list-before-pending",
        )
        before_count = len(page.get("items") or [])
        needed = max(0, TARGET_TOTAL - before_count)
        if needed < 3:
            raise RuntimeError(
                "Need at least append, cancel, and append-race objects; inspect current state"
            )
        labels = ["append_page", "cancel_page", "append_race"] + [
            f"filler_{index:02d}" for index in range(1, needed - 2)
        ]
        created_ids: list[int] = []
        for label in labels:
            marker = f"{context['run_id']}:{label}"
            created = _create(
                client,
                token=token,
                house_id=int(context["org"]["house_id"]),
                marker=marker,
                label=f"create-{label}",
            )
            if created["status"] != "PENDING_ANALYSIS":
                raise RuntimeError(
                    f"Worker was not paused before pending preparation: {label}={created['status']}"
                )
            work_order_id = int(created["id"])
            created_ids.append(work_order_id)
            context["objects"][f"{label}_id"] = work_order_id
            context["objects"][f"{label}_marker_hash"] = _hash(marker)
            _save_private(context)
        final_page = client.data(
            "GET",
            "/work-orders",
            token=token,
            params={"after_id": 0, "limit": 100},
            label="list-after-pending",
        )
        after_count = len(final_page.get("items") or [])
        if after_count != before_count + needed or after_count < TARGET_TOTAL:
            raise RuntimeError("Resident order count did not match the bounded preparation")
        context.setdefault("phases", {})["pending"] = {
            "completed": True,
            "worker_expected_paused": True,
            "before_count": before_count,
            "created_count": needed,
            "after_count": after_count,
            "created_ids": created_ids,
            "all_initial_statuses": "PENDING_ANALYSIS",
        }
        _save_private(context)
        _save_public(context, client)
    finally:
        client.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("rating", "pending"))
    args = parser.parse_args()
    if args.phase == "rating":
        _rating_phase()
    else:
        _pending_phase()


if __name__ == "__main__":
    main()
