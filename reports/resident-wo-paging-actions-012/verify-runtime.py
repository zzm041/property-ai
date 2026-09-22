"""Collect redacted API concurrency/negative and read-only database evidence."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import re
import site
import subprocess
import sys
import threading
from urllib.parse import urlsplit


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


TASK_ID = "RESIDENT-WO-PAGING-ACTIONS-012"
API = "http://127.0.0.1:8083/api/v1"
PRIVATE_CONTEXT = REPO / "tmp" / "resident-wo-paging-actions-012" / "context.json"
API_EVIDENCE = Path(__file__).with_name("api-negative-concurrency-evidence.json")
DB_EVIDENCE = Path(__file__).with_name("database-evidence.json")


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


class Client:
    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self.http = httpx.Client(base_url=API, timeout=30.0)
        self.records: list[dict[str, object]] = []

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
        expected: tuple[int, ...] = (200,),
    ) -> tuple[int, dict[str, object]]:
        request_id = f"{self.run_id}-verify-{label}"
        headers = {"X-Request-Id": request_id}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        response = self.http.request(
            method,
            path,
            headers=headers,
            json=body,
            params=params,
        )
        try:
            payload = response.json()
        except ValueError as exc:
            raise RuntimeError(f"{label} returned non-JSON HTTP {response.status_code}") from exc
        record = {
            "label": label,
            "method": method,
            "path": path,
            "status": response.status_code,
            "error_code": _error_code(payload),
            "request_id_hash": _hash(request_id),
        }
        self.records.append(record)
        if response.status_code not in expected:
            raise RuntimeError(
                f"{label} failed: HTTP {response.status_code} code={record['error_code']}"
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
            label=f"{label}-send",
        )
        inbox = self.data(
            "POST",
            "/auth/sms/test-inbox",
            body={"phone": phone, "purpose": "login"},
            label=f"{label}-inbox",
        )
        return self.data(
            "POST",
            "/auth/sms/login",
            body={"phone": phone, "code": inbox["code"]},
            label=f"{label}-login",
        )


def _expect_error(
    client: Client,
    method: str,
    path: str,
    *,
    label: str,
    token: str,
    body: dict[str, object] | None,
    expected_status: int,
    expected_code: str,
    params: dict[str, object] | None = None,
) -> dict[str, object]:
    status, payload = client.call(
        method,
        path,
        label=label,
        token=token,
        body=body,
        params=params,
        expected=(expected_status,),
    )
    code = _error_code(payload)
    if status != expected_status or code != expected_code:
        raise RuntimeError(
            f"{label} expected {expected_status}/{expected_code}; got {status}/{code}"
        )
    return {"status": status, "error_code": code}


def _race(
    *,
    run_id: str,
    token: str,
    path: str,
    body: dict[str, object],
    label: str,
) -> list[dict[str, object]]:
    barrier = threading.Barrier(2)

    def issue(index: int) -> dict[str, object]:
        request_id = f"{run_id}-verify-{label}-{index}"
        barrier.wait(timeout=10)
        with httpx.Client(base_url=API, timeout=30.0) as http:
            response = http.post(
                path,
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-Request-Id": request_id,
                },
                json=body,
            )
            payload = response.json()
        return {
            "attempt": index,
            "status": response.status_code,
            "error_code": _error_code(payload),
            "request_id_hash": _hash(request_id),
        }

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(issue, (1, 2)))
    return sorted(results, key=lambda item: (int(item["status"]), int(item["attempt"])))


def _api_phase() -> None:
    _guard()
    context = json.loads(PRIVATE_CONTEXT.read_text(encoding="utf-8"))
    run_id = str(context["run_id"])
    objects = context["objects"]
    client = Client(run_id)
    try:
        primary = client.sms_login(str(context["phones"]["primary"]), "primary")
        secondary = client.sms_login(str(context["phones"]["secondary"]), "secondary")
        primary_token = str(primary["access_token"])
        secondary_token = str(secondary["access_token"])
        if int(primary["user_id"]) != int(context["identities"]["primary"]):
            raise RuntimeError("Primary login identity changed")
        if int(secondary["user_id"]) != int(context["identities"]["secondary"]):
            raise RuntimeError("Secondary login identity changed")

        first = client.data(
            "GET",
            "/work-orders",
            token=primary_token,
            params={"after_id": 0, "limit": 20},
            label="page-first",
        )
        first_ids = [int(item["id"]) for item in first["items"]]
        if len(first_ids) != 20 or len(set(first_ids)) != 20 or first_ids != sorted(first_ids):
            raise RuntimeError("First page is not 20 unique ascending items")
        if int(first["next_cursor"]) != first_ids[-1]:
            raise RuntimeError("First page cursor is not its last ID")
        second = client.data(
            "GET",
            "/work-orders",
            token=primary_token,
            params={"after_id": first["next_cursor"], "limit": 20},
            label="page-second",
        )
        second_ids = [int(item["id"]) for item in second["items"]]
        if len(second_ids) != 2 or second["next_cursor"] is not None:
            raise RuntimeError("Final page boundary or cursor is incorrect")
        if set(first_ids).intersection(second_ids) or not all(
            item > first_ids[-1] for item in second_ids
        ):
            raise RuntimeError("Second page overlaps or does not advance")
        empty = client.data(
            "GET",
            "/work-orders",
            token=primary_token,
            params={"after_id": second_ids[-1], "limit": 20},
            label="page-after-final",
        )
        if empty["items"] or empty["next_cursor"] is not None:
            raise RuntimeError("After-final cursor did not return an empty final page")
        limit_zero = _expect_error(
            client,
            "GET",
            "/work-orders",
            label="page-limit-zero",
            token=primary_token,
            body=None,
            params={"after_id": 0, "limit": 0},
            expected_status=422,
            expected_code="SYS.VALIDATION_ERROR",
        )
        limit_high = _expect_error(
            client,
            "GET",
            "/work-orders",
            label="page-limit-high",
            token=primary_token,
            body=None,
            params={"after_id": 0, "limit": 101},
            expected_status=422,
            expected_code="SYS.VALIDATION_ERROR",
        )

        append_id = int(objects["append_page_id"])
        cancel_id = int(objects["cancel_page_id"])
        append_race_id = int(objects["append_race_id"])
        rating_page_id = int(objects["rating_page_id"])
        rating_race_id = int(objects["rating_race_id"])
        filler_id = int(objects["filler_01_id"])

        append_detail = client.data(
            "GET",
            f"/work-orders/{append_id}",
            token=primary_token,
            label="append-detail",
        )
        append_version = int(append_detail["work_order"]["version"])
        append_empty = _expect_error(
            client,
            "POST",
            f"/work-orders/{append_id}/append",
            label="append-empty",
            token=primary_token,
            body={"text": None, "file_ids": [], "version": append_version},
            expected_status=422,
            expected_code="WO.CONTENT_REQUIRED",
        )
        append_phase = _expect_error(
            client,
            "POST",
            f"/work-orders/{append_id}/append",
            label="append-invalid-phase",
            token=primary_token,
            body={
                "text": "synthetic negative",
                "file_ids": [],
                "taken_phase": "AFTER",
                "version": append_version,
            },
            expected_status=422,
            expected_code="WO.INVALID_INPUT",
        )
        append_foreign = _expect_error(
            client,
            "POST",
            f"/work-orders/{append_id}/append",
            label="append-foreign-resident",
            token=secondary_token,
            body={
                "text": "synthetic foreign append",
                "file_ids": [],
                "taken_phase": "REPORT",
                "version": append_version,
            },
            expected_status=403,
            expected_code="IAM.FORBIDDEN",
        )
        detail_foreign = _expect_error(
            client,
            "GET",
            f"/work-orders/{append_id}",
            label="detail-foreign-resident",
            token=secondary_token,
            body=None,
            expected_status=403,
            expected_code="IAM.FORBIDDEN",
        )
        terminal_detail = client.data(
            "GET",
            f"/work-orders/{cancel_id}",
            token=primary_token,
            label="cancelled-detail",
        )
        append_terminal = _expect_error(
            client,
            "POST",
            f"/work-orders/{cancel_id}/append",
            label="append-terminal",
            token=primary_token,
            body={
                "text": "synthetic terminal append",
                "file_ids": [],
                "taken_phase": "REPORT",
                "version": int(terminal_detail["work_order"]["version"]),
            },
            expected_status=409,
            expected_code="WO.ALREADY_PROCESSED",
        )

        append_race_detail = client.data(
            "GET",
            f"/work-orders/{append_race_id}",
            token=primary_token,
            label="append-race-detail-before",
        )
        append_race_marker = f"{run_id}:api-append-race"
        append_race = _race(
            run_id=run_id,
            token=primary_token,
            path=f"/work-orders/{append_race_id}/append",
            body={
                "text": append_race_marker,
                "file_ids": [],
                "taken_phase": "REPORT",
                "version": int(append_race_detail["work_order"]["version"]),
            },
            label="append-race",
        )
        if [item["status"] for item in append_race] != [200, 409] or {
            item["error_code"] for item in append_race
        } != {None, "WO.CONFLICT"}:
            raise RuntimeError(f"Append concurrency result was not one success/one conflict: {append_race}")
        append_race_after = client.data(
            "GET",
            f"/work-orders/{append_race_id}",
            token=primary_token,
            label="append-race-detail-after",
        )
        if str(append_race_after.get("extra_notes") or "").count(append_race_marker) != 1:
            raise RuntimeError("Concurrent append marker was not persisted exactly once")

        filler_detail = client.data(
            "GET",
            f"/work-orders/{filler_id}",
            token=primary_token,
            label="filler-detail",
        )
        filler_version = int(filler_detail["work_order"]["version"])
        cancel_stale = _expect_error(
            client,
            "POST",
            f"/work-orders/{filler_id}/cancel",
            label="cancel-stale",
            token=primary_token,
            body={"reason": "stale", "version": filler_version + 1},
            expected_status=409,
            expected_code="WO.CONFLICT",
        )
        cancel_foreign = _expect_error(
            client,
            "POST",
            f"/work-orders/{filler_id}/cancel",
            label="cancel-foreign-resident",
            token=secondary_token,
            body={"reason": "not owner", "version": filler_version},
            expected_status=403,
            expected_code="IAM.FORBIDDEN",
        )
        cancel_repeat = _expect_error(
            client,
            "POST",
            f"/work-orders/{cancel_id}/cancel",
            label="cancel-repeat",
            token=primary_token,
            body={
                "reason": "repeat",
                "version": int(terminal_detail["work_order"]["version"]),
            },
            expected_status=409,
            expected_code="WO.INVALID_TRANSITION",
        )
        assigned_detail = client.data(
            "GET",
            "/work-orders/1",
            token=primary_token,
            label="assigned-detail",
        )
        if assigned_detail["work_order"]["status"] != "ASSIGNED":
            raise RuntimeError("Expected existing synthetic ASSIGNED boundary object changed")
        cancel_assigned = _expect_error(
            client,
            "POST",
            "/work-orders/1/cancel",
            label="cancel-assigned",
            token=primary_token,
            body={
                "reason": "illegal state",
                "version": int(assigned_detail["work_order"]["version"]),
            },
            expected_status=409,
            expected_code="WO.INVALID_TRANSITION",
        )

        rate_early = _expect_error(
            client,
            "POST",
            f"/work-orders/{filler_id}/rate",
            label="rate-early",
            token=primary_token,
            body={"rating": 3, "comment": "too early"},
            expected_status=409,
            expected_code="WO.INVALID_TRANSITION",
        )
        rate_foreign = _expect_error(
            client,
            "POST",
            f"/work-orders/{rating_page_id}/rate",
            label="rate-foreign-resident",
            token=secondary_token,
            body={"rating": 3, "comment": "not owner"},
            expected_status=403,
            expected_code="IAM.FORBIDDEN",
        )
        rate_zero = _expect_error(
            client,
            "POST",
            f"/work-orders/{rating_race_id}/rate",
            label="rate-zero",
            token=primary_token,
            body={"rating": 0, "comment": None},
            expected_status=422,
            expected_code="SYS.VALIDATION_ERROR",
        )
        rate_six = _expect_error(
            client,
            "POST",
            f"/work-orders/{rating_race_id}/rate",
            label="rate-six",
            token=primary_token,
            body={"rating": 6, "comment": None},
            expected_status=422,
            expected_code="SYS.VALIDATION_ERROR",
        )
        rating_race_marker = f"{run_id}:api-rating-race"
        rating_race = _race(
            run_id=run_id,
            token=primary_token,
            path=f"/work-orders/{rating_race_id}/rate",
            body={"rating": 4, "comment": rating_race_marker},
            label="rating-race",
        )
        if [item["status"] for item in rating_race] != [200, 409] or {
            item["error_code"] for item in rating_race
        } != {None, "WO.ALREADY_PROCESSED"}:
            raise RuntimeError(f"Rating concurrency result was not one success/one rejection: {rating_race}")
        rating_repeat = _expect_error(
            client,
            "POST",
            f"/work-orders/{rating_page_id}/rate",
            label="rate-repeat",
            token=primary_token,
            body={"rating": 5, "comment": "repeat"},
            expected_status=409,
            expected_code="WO.ALREADY_PROCESSED",
        )

        evidence = {
            "task_id": TASK_ID,
            "captured_at": datetime.now(UTC).isoformat(),
            "code_sha": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=REPO, text=True
            ).strip(),
            "run_id": run_id,
            "environment": {
                "api": API,
                "database": "127.0.0.1:3307/property_ai_auth001_test",
                "revision": "20260919_0009",
                "rabbit_vhost": ISOLATED_VHOST,
                "rabbit_queue": ISOLATED_QUEUE,
                "worker_during_negative_checks": "paused; pending state stable",
            },
            "identities": {
                "primary": int(primary["user_id"]),
                "secondary": int(secondary["user_id"]),
            },
            "objects": {
                "append_id": append_id,
                "cancel_id": cancel_id,
                "append_race_id": append_race_id,
                "filler_boundary_id": filler_id,
                "assigned_boundary_id": 1,
                "rating_page_id": rating_page_id,
                "rating_race_id": rating_race_id,
                "append_race_marker_hash": _hash(append_race_marker),
                "rating_race_comment_hash": _hash(rating_race_marker),
            },
            "pagination": {
                "first_count": len(first_ids),
                "first_cursor": first["next_cursor"],
                "second_count": len(second_ids),
                "second_cursor": second["next_cursor"],
                "after_final_count": len(empty["items"]),
                "after_final_cursor": empty["next_cursor"],
                "unique_across_pages": len(set(first_ids + second_ids))
                == len(first_ids + second_ids),
                "limit_zero": limit_zero,
                "limit_101": limit_high,
            },
            "append": {
                "empty": append_empty,
                "invalid_phase": append_phase,
                "foreign_resident": append_foreign,
                "terminal": append_terminal,
                "concurrent_same_version": append_race,
                "concurrent_marker_occurrences": 1,
            },
            "cancel": {
                "stale_version": cancel_stale,
                "foreign_resident": cancel_foreign,
                "repeat": cancel_repeat,
                "assigned_illegal_state": cancel_assigned,
            },
            "rating": {
                "before_terminal": rate_early,
                "foreign_resident": rate_foreign,
                "rating_zero": rate_zero,
                "rating_six": rate_six,
                "concurrent_first_rating": rating_race,
                "repeat_page_rating": rating_repeat,
            },
            "detail_foreign_resident": detail_foreign,
            "requests": client.records,
            "boundaries": {
                "normal_sms_login": True,
                "seed_or_dev_token_login": False,
                "direct_database_business_write": False,
                "real_model_claimed": False,
                "production_claimed": False,
            },
        }
        API_EVIDENCE.write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    finally:
        client.close()


def _db_phase() -> None:
    settings = _guard()
    context = json.loads(PRIVATE_CONTEXT.read_text(encoding="utf-8"))
    run_id = str(context["run_id"])
    objects = context["objects"]
    ids = [
        int(objects[key])
        for key in (
            "rating_page_id",
            "rating_race_id",
            "append_page_id",
            "cancel_page_id",
            "append_race_id",
        )
    ]
    append_marker = f"{run_id}:page-append"
    cancel_reason = f"{run_id}:page-cancel"
    rating_comment = f"{run_id}:page-rating"
    append_race_marker = f"{run_id}:api-append-race"
    rating_race_comment = f"{run_id}:api-rating-race"
    engine = create_engine(settings.test_database_url)
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql("SET TRANSACTION READ ONLY")
            revision = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
            orders = [
                dict(row)
                for row in connection.execute(
                    text(
                        "SELECT id, reporter_user_id, tenant_id, property_id, house_id, "
                        "status, version, current_assignment_id, closed_at "
                        "FROM work_orders WHERE id IN :ids ORDER BY id"
                    ).bindparams(bindparam("ids", expanding=True)),
                    {"ids": ids},
                ).mappings()
            ]
            contents = [
                dict(row)
                for row in connection.execute(
                    text(
                        "SELECT work_order_id, extra_notes FROM work_order_contents "
                        "WHERE work_order_id IN :ids ORDER BY work_order_id"
                    ).bindparams(bindparam("ids", expanding=True)),
                    {"ids": ids},
                ).mappings()
            ]
            logs = [
                dict(row)
                for row in connection.execute(
                    text(
                        "SELECT work_order_id, actor_user_id, actor_type, from_status, "
                        "to_status, event_code, payload_json, created_at "
                        "FROM work_order_logs WHERE work_order_id IN :ids ORDER BY id"
                    ).bindparams(bindparam("ids", expanding=True)),
                    {"ids": ids},
                ).mappings()
            ]
            feedbacks = [
                dict(row)
                for row in connection.execute(
                    text(
                        "SELECT work_order_id, actor_user_id, confirmed, rating, comment, "
                        "unresolved_flag, created_at FROM work_order_feedbacks "
                        "WHERE work_order_id IN :ids ORDER BY id"
                    ).bindparams(bindparam("ids", expanding=True)),
                    {"ids": ids},
                ).mappings()
            ]
            assignments = [
                dict(row)
                for row in connection.execute(
                    text(
                        "SELECT work_order_id, employee_id, assigned_by_user_id, status, "
                        "reason, ended_at FROM work_order_assignments "
                        "WHERE work_order_id IN :ids ORDER BY id"
                    ).bindparams(bindparam("ids", expanding=True)),
                    {"ids": ids},
                ).mappings()
            ]
            processes = [
                dict(row)
                for row in connection.execute(
                    text(
                        "SELECT work_order_id, cycle_no, assignee_id, started_at, ended_at "
                        "FROM work_order_processes WHERE work_order_id IN :ids ORDER BY id"
                    ).bindparams(bindparam("ids", expanding=True)),
                    {"ids": ids},
                ).mappings()
            ]
            receipts = [
                dict(row)
                for row in connection.execute(
                    text(
                        "SELECT work_order_id, problem_type, method, fee_flag, resolved, "
                        "finished_at FROM work_order_receipts WHERE work_order_id IN :ids "
                        "ORDER BY id"
                    ).bindparams(bindparam("ids", expanding=True)),
                    {"ids": ids},
                ).mappings()
            ]
            audits = [
                dict(row)
                for row in connection.execute(
                    text(
                        "SELECT actor_user_id, action, resource_type, resource_id, "
                        "tenant_id, property_id, created_at FROM audit_logs "
                        "WHERE resource_type='WORK_ORDER' AND resource_id IN :ids "
                        "ORDER BY id"
                    ).bindparams(bindparam("ids", expanding=True)),
                    {"ids": ids},
                ).mappings()
            ]
    finally:
        engine.dispose()

    content_evidence = []
    for row in contents:
        notes = str(row.pop("extra_notes") or "")
        content_evidence.append(
            {
                **row,
                "notes_length": len(notes),
                "notes_hash": _hash(notes),
                "page_marker_occurrences": notes.count(append_marker),
                "race_marker_occurrences": notes.count(append_race_marker),
            }
        )
    log_evidence = []
    for row in logs:
        payload = row.pop("payload_json") or {}
        if isinstance(payload, str):
            payload = json.loads(payload)
        reason = str(payload.get("reason") or "") if isinstance(payload, dict) else ""
        log_evidence.append(
            {
                **row,
                "reason_hash": _hash(reason) if reason else None,
            }
        )
    feedback_evidence = []
    for row in feedbacks:
        comment = str(row.pop("comment") or "")
        feedback_evidence.append(
            {
                **row,
                "comment_hash": _hash(comment) if comment else None,
            }
        )

    rating_page_id = int(objects["rating_page_id"])
    rating_race_id = int(objects["rating_race_id"])
    append_page_id = int(objects["append_page_id"])
    cancel_page_id = int(objects["cancel_page_id"])
    append_race_id = int(objects["append_race_id"])
    by_order = {int(row["id"]): row for row in orders}
    evidence = {
        "task_id": TASK_ID,
        "captured_at": datetime.now(UTC).isoformat(),
        "code_sha": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=REPO, text=True
        ).strip(),
        "run_id": run_id,
        "environment": {
            "database": "127.0.0.1:3307/property_ai_auth001_test",
            "revision": revision,
            "read_only_transaction": True,
            "rabbit_vhost": ISOLATED_VHOST,
            "rabbit_queue": ISOLATED_QUEUE,
        },
        "objects": {
            "rating_page_id": rating_page_id,
            "rating_race_id": rating_race_id,
            "append_page_id": append_page_id,
            "cancel_page_id": cancel_page_id,
            "append_race_id": append_race_id,
        },
        "work_orders": orders,
        "contents": content_evidence,
        "logs": log_evidence,
        "feedbacks": feedback_evidence,
        "assignments": assignments,
        "processes": processes,
        "receipts": receipts,
        "audits": audits,
        "assertions": {
            "revision_is_0009": revision == "20260919_0009",
            "all_objects_present": len(orders) == len(ids),
            "page_append_marker_once": next(
                item for item in content_evidence if int(item["work_order_id"]) == append_page_id
            )["page_marker_occurrences"]
            == 1,
            "concurrent_append_marker_once": next(
                item for item in content_evidence if int(item["work_order_id"]) == append_race_id
            )["race_marker_occurrences"]
            == 1,
            "cancelled_state_persisted": by_order[cancel_page_id]["status"] == "CANCELLED",
            "cancel_reason_hash_matches": any(
                int(item["work_order_id"]) == cancel_page_id
                and item["event_code"] == "CANCEL"
                and item["reason_hash"] == _hash(cancel_reason)
                for item in log_evidence
            ),
            "one_page_rating": sum(
                1
                for item in feedback_evidence
                if int(item["work_order_id"]) == rating_page_id and item["rating"] is not None
            )
            == 1,
            "page_rating_matches": any(
                int(item["work_order_id"]) == rating_page_id
                and int(item["rating"] or 0) == 3
                and item["comment_hash"] == _hash(rating_comment)
                for item in feedback_evidence
            ),
            "one_concurrent_rating": sum(
                1
                for item in feedback_evidence
                if int(item["work_order_id"]) == rating_race_id and item["rating"] is not None
            )
            == 1,
            "concurrent_rating_matches": any(
                int(item["work_order_id"]) == rating_race_id
                and int(item["rating"] or 0) == 4
                and item["comment_hash"] == _hash(rating_race_comment)
                for item in feedback_evidence
            ),
            "rating_objects_remain_closed": by_order[rating_page_id]["status"] == "CLOSED"
            and by_order[rating_race_id]["status"] == "CLOSED",
            "rating_objects_have_auto_assignments": all(
                any(
                    int(item["work_order_id"]) == target
                    and item["assigned_by_user_id"] is None
                    and item["reason"] == "AUTO_DISPATCH"
                    for item in assignments
                )
                for target in (rating_page_id, rating_race_id)
            ),
            "rating_objects_have_process_and_receipt": all(
                any(int(item["work_order_id"]) == target for item in processes)
                and any(
                    int(item["work_order_id"]) == target and bool(item["resolved"])
                    for item in receipts
                )
                for target in (rating_page_id, rating_race_id)
            ),
            "cancel_audit_present": any(
                int(item["resource_id"] or 0) == cancel_page_id
                and item["action"] == "WORK_ORDER_CANCELLED"
                for item in audits
            ),
        },
        "hashes": {
            "page_append_marker": _hash(append_marker),
            "concurrent_append_marker": _hash(append_race_marker),
            "cancel_reason": _hash(cancel_reason),
            "page_rating_comment": _hash(rating_comment),
            "concurrent_rating_comment": _hash(rating_race_comment),
        },
        "boundaries": {
            "direct_database_business_write": False,
            "database_evidence_only": True,
            "phone_token_otp_or_resident_text_included": False,
            "real_model_claimed": False,
            "production_claimed": False,
        },
    }
    if not all(evidence["assertions"].values()):
        raise RuntimeError(f"Database evidence assertion failed: {evidence['assertions']}")
    DB_EVIDENCE.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("api", "db"))
    args = parser.parse_args()
    if args.phase == "api":
        _api_phase()
    else:
        _db_phase()


if __name__ == "__main__":
    main()
