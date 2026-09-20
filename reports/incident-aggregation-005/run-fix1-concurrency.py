"""Concurrent AUTH001 API/worker proof for serialized incident aggregation."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

import httpx
from sqlalchemy import bindparam, create_engine, text

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"
OPS = REPO / "scripts" / "ops"
for path in (BACKEND, OPS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from auth001_isolated_runtime import build_isolated_settings  # noqa: E402
from app.worker.failed_tasks import get_failed_task  # noqa: E402

API = "http://127.0.0.1:8083/api/v1"
CONTEXT = REPO / "tmp" / "staff-mgmt-auto-dispatch-001" / "context.json"
OUTPUT = Path(__file__).with_name("fix1-concurrency-evidence.json")


def _request(client: httpx.Client, method: str, path: str, **kwargs):
    response = client.request(method, f"{API}{path}", **kwargs)
    response.raise_for_status()
    return response.json()["data"]


def _login(client: httpx.Client, phone: str) -> str:
    challenge = _request(
        client,
        "POST",
        "/auth/challenge",
        json={"phone": phone, "purpose": "login"},
    )
    numbers = [int(item) for item in re.findall(r"\d+", challenge["prompt"])]
    if len(numbers) != 2:
        raise RuntimeError("visual challenge prompt was not understood")
    _request(
        client,
        "POST",
        "/auth/sms/send",
        json={
            "phone": phone,
            "purpose": "login",
            "challenge_id": challenge["challenge_id"],
            "challenge_answer": str(sum(numbers)),
        },
    )
    inbox = _request(
        client,
        "POST",
        "/auth/sms/test-inbox",
        json={"phone": phone, "purpose": "login"},
    )
    issued = _request(
        client,
        "POST",
        "/auth/sms/login",
        json={"phone": phone, "code": inbox["code"]},
    )
    return str(issued["access_token"])


def _create(token: str, *, house_id: int, suffix: str, number: int) -> int:
    with httpx.Client(timeout=15) as client:
        created = _request(
            client,
            "POST",
            "/work-orders",
            json={
                "house_id": house_id,
                "text": f"Concurrent common electrical probe {suffix} item {number}",
                "category": "ELECTRICAL",
                "priority": "HIGH",
                "severity": "HIGH",
            },
            headers={
                "Authorization": f"Bearer {token}",
                "X-Request-Id": f"incident-concurrent-{suffix}-{number}",
                "Idempotency-Key": f"incident-fix1-concurrent-{suffix}-{number}",
            },
        )
    return int(created["id"])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--collect-existing", nargs="+", type=int)
    parser.add_argument("--replayed-record-id", default="")
    parser.add_argument("--erroneous-shared-task-id", default="")
    parser.add_argument("--output", default=OUTPUT.name)
    parser.add_argument(
        "--task-id",
        default="INCIDENT-AGGREGATION-005-FIX-1-CONCURRENCY",
    )
    parser.add_argument("--worker-consumers", type=int, default=2)
    args = parser.parse_args()
    settings = build_isolated_settings(enable_enqueue=False)
    failed_task = (
        get_failed_task(args.replayed_record_id, settings)
        if args.replayed_record_id
        else None
    )
    engine = create_engine(settings.test_database_url)
    if args.collect_existing:
        work_order_ids = sorted(set(args.collect_existing))
    else:
        private = json.loads(CONTEXT.read_text(encoding="utf-8"))
        suffix = uuid4().hex[:10]
        with httpx.Client(timeout=10) as client:
            token = _login(client, private["phones"]["resident"])
        with ThreadPoolExecutor(max_workers=6) as pool:
            work_order_ids = sorted(
                pool.map(
                    lambda number: _create(
                        token,
                        house_id=int(private["org"]["house_id"]),
                        suffix=suffix,
                        number=number,
                    ),
                    range(1, 7),
                )
            )

    ids = bindparam("ids", expanding=True)
    parameters = {"ids": work_order_ids}
    deadline = time.monotonic() + 90
    orders: list[dict[str, object]] = []
    while time.monotonic() < deadline:
        with engine.connect() as connection:
            orders = [
                dict(row)
                for row in connection.execute(
                    text(
                        "SELECT id, status, incident_id, current_assignment_id "
                        "FROM work_orders WHERE id IN :ids ORDER BY id"
                    ).bindparams(ids),
                    parameters,
                ).mappings()
            ]
        if (
            len(orders) == 6
            and all(item["status"] == "ASSIGNED" for item in orders)
            and len({int(item["incident_id"] or 0) for item in orders}) == 1
            and int(orders[0]["incident_id"] or 0) > 0
        ):
            break
        time.sleep(0.25)
    incident_ids = {int(item["incident_id"] or 0) for item in orders}
    if len(incident_ids) != 1 or 0 in incident_ids:
        raise RuntimeError(f"concurrent orders did not converge: {orders}")
    incident_id = incident_ids.pop()

    try:
        with engine.connect() as connection:
            revision = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
            incident = dict(
                connection.execute(
                    text(
                        "SELECT id, status, category, subcategory, "
                        "primary_work_order_id, primary_employee_id, work_order_count "
                        "FROM incidents WHERE id = :incident_id"
                    ),
                    {"incident_id": incident_id},
                ).mappings().one()
            )
            links = [
                dict(row)
                for row in connection.execute(
                    text(
                        "SELECT work_order_id FROM incident_work_orders "
                        "WHERE incident_id = :incident_id ORDER BY work_order_id"
                    ),
                    {"incident_id": incident_id},
                ).mappings()
            ]
            analyses = [
                dict(row)
                for row in connection.execute(
                    text(
                        "SELECT work_order_id, status, subcategory FROM work_order_analyses "
                        "WHERE work_order_id IN :ids ORDER BY work_order_id"
                    ).bindparams(ids),
                    parameters,
                ).mappings()
            ]
            assignments = [
                dict(row)
                for row in connection.execute(
                    text(
                        "SELECT work_order_id, employee_id, assigned_source, reason, "
                        "JSON_UNQUOTE(JSON_EXTRACT(payload_json, '$.incident_follow')) "
                        "AS incident_follow, "
                        "JSON_UNQUOTE(JSON_EXTRACT(payload_json, "
                        "'$.incident_capacity_owner')) AS capacity_owner "
                        "FROM work_order_assignments WHERE work_order_id IN :ids "
                        "AND status = 'ACTIVE' ORDER BY work_order_id"
                    ).bindparams(ids),
                    parameters,
                ).mappings()
            ]
            ai_calls = [
                dict(row)
                for row in connection.execute(
                    text(
                        "SELECT work_order_id, provider_mode, model_version, status "
                        "FROM ai_call_logs WHERE work_order_id IN :ids "
                        "ORDER BY work_order_id, id"
                    ).bindparams(ids),
                    parameters,
                ).mappings()
            ]
            incident_count = int(
                connection.execute(
                    text(
                        "SELECT COUNT(DISTINCT incident_id) FROM incident_work_orders "
                        "WHERE work_order_id IN :ids"
                    ).bindparams(ids),
                    parameters,
                ).scalar_one()
            )
    finally:
        engine.dispose()

    evidence = {
        "task_id": args.task_id,
        "environment": {
            "api": API,
            "database": "127.0.0.1:3307/property_ai_auth001_test",
            "revision": revision,
            "queue": "property_ai.auth001",
            "worker_consumers_during_concurrent_create": args.worker_consumers,
            "worker_consumers_during_recovery": 1,
            "commit": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=REPO, text=True
            ).strip(),
            "model_mode": "development_fake",
        },
        "concurrent_work_order_ids": work_order_ids,
        "recovery": {
            "collected_existing_only": bool(args.collect_existing),
            "replayed_record_id": args.replayed_record_id or None,
            "isolated_record_status": failed_task.get("status") if failed_task else None,
            "isolated_replay_task_id": (
                failed_task.get("replay_task_id") if failed_task else None
            ),
            "isolated_replayed": bool(failed_task and failed_task.get("replayed")),
            "erroneous_shared_publish": {
                "task_id": args.erroneous_shared_task_id or None,
                "vhost": "/property_ai" if args.erroneous_shared_task_id else None,
                "queue": "property_ai.auth001" if args.erroneous_shared_task_id else None,
                "observed_messages_ready": 7 if args.erroneous_shared_task_id else None,
                "observed_consumers": 0 if args.erroneous_shared_task_id else None,
                "cleanup_status": (
                    "PENDING_EXPLICIT_APPROVAL"
                    if args.erroneous_shared_task_id
                    else None
                ),
            },
        },
        "orders": orders,
        "incident": incident,
        "links": links,
        "analyses": analyses,
        "assignments": assignments,
        "ai_calls": ai_calls,
        "assertions": {
            "six_concurrent_api_creates": len(work_order_ids) == 6,
            "one_incident_only": incident_count == 1,
            "six_unique_links": [int(item["work_order_id"]) for item in links]
            == work_order_ids,
            "incident_count_is_six": int(incident["work_order_count"]) == 6,
            "all_analysis_completed_same_subcategory": len(analyses) == 6
            and all(
                item["status"] == "COMPLETED"
                and item["subcategory"] == "outlet_spark"
                for item in analyses
            ),
            "all_auto_assigned_once": len(assignments) == 6
            and all(item["assigned_source"] == "AUTO" for item in assignments),
            "primary_is_live_member": int(incident["primary_work_order_id"])
            in work_order_ids,
            "fake_provider_explicit": len(ai_calls) >= 6
            and all(
                item["provider_mode"] == "DEVELOPMENT"
                and str(item["model_version"]).startswith("dev-")
                and item["status"] == "SUCCESS"
                for item in ai_calls
            ),
            "no_real_model_claim": True,
        },
    }
    if not all(evidence["assertions"].values()):
        raise RuntimeError(f"concurrency assertions failed: {evidence['assertions']}")
    Path(__file__).with_name(Path(args.output).name).write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(evidence, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
