"""Controlled analyze-failure and replay drill against the Day 4 stack.

Path: HTTP/Nginx → API → RabbitMQ → Worker. Fake Development LLM only.
Does not stop shared RabbitMQ/Redis and does not open staging DevAuth.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

import httpx
from sqlalchemy import create_engine, text

REPO = Path(__file__).resolve().parents[2]
COMPOSE = REPO / "deploy" / "docker-compose.day4.yml"
BASE = os.environ.get("DAY4_BASE_URL", "").rstrip("/")
WORKER = os.environ.get("DAY4_WORKER_CONTAINER", "property-ai-day4-worker-day4-1")


def _compose(args: list[str], extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE), *args],
        cwd=str(REPO / "deploy"),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _token(client: httpx.Client, user_id: int) -> str:
    response = client.post(
        f"{BASE}/api/v1/auth/dev/token",
        json={"user_id": user_id},
        headers={"X-Request-Id": f"d4-failq-login-{user_id}"},
        timeout=20.0,
    )
    if response.status_code != 200:
        raise SystemExit(f"dev token status={response.status_code}")
    token = str((response.json().get("data") or {}).get("access_token") or "")
    if not token:
        raise SystemExit("dev token missing")
    return token


def _list_failed() -> str:
    result = subprocess.run(
        ["docker", "exec", WORKER, "python", "-m", "app.cli.replay_failed_task", "--list"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return f"<list_rc={result.returncode} {(result.stderr or result.stdout or '')[:300]}>"
    return (result.stdout or "").strip()


def _lookup_resident() -> dict[str, int]:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        raise SystemExit("TEST_DATABASE_URL is required")
    engine = create_engine(url)
    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT user_id, house_id FROM resident_house_relations "
                "WHERE status='ACTIVE' AND valid_to IS NULL "
                "ORDER BY id DESC LIMIT 1"
            )
        ).first()
        if row is None:
            raise SystemExit("no ACTIVE house relation in Day 4 database")
        user_id, house_id = int(row[0]), int(row[1])
    engine.dispose()
    return {"user_id": user_id, "house_id": house_id}


def _wo_status(connection_url: str, work_order_id: int) -> str:
    engine = create_engine(connection_url)
    with engine.connect() as connection:
        status = connection.execute(
            text("SELECT status FROM work_orders WHERE id=:id"),
            {"id": work_order_id},
        ).scalar()
    engine.dispose()
    return str(status or "")


def _enqueue_fail_compensation(evidence: list[str], ids: dict[str, int]) -> bool:
    """WO commit + broker enqueue failure: recover from Redis record if present."""

    ok = True
    record_id = ""
    work_order_id = None
    recreate = _compose(
        ["up", "-d", "--no-deps", "--force-recreate", "api-day4"],
        {
            "DAY4_CELERY_BROKER_URL": "amqp://day4:wrong-password@rabbitmq:5672/property_ai_day4",
        },
    )
    evidence.append(f"api_bad_broker_recreate={recreate.returncode}")
    if recreate.returncode != 0:
        evidence.append(f"api_bad_broker_recreate_stderr={(recreate.stderr or '')[-200:]}")
        return False
    try:
        time.sleep(8)
        with httpx.Client(timeout=30.0) as client:
            resident = _token(client, ids["user_id"])
            created = client.post(
                f"{BASE}/api/v1/work-orders",
                json={
                    "house_id": ids["house_id"],
                    "text": "Enqueue failure compensation drill",
                    "category": "ELECTRICAL",
                },
                headers={
                    "Authorization": f"Bearer {resident}",
                    "Idempotency-Key": f"d4-fail-enqueue-{uuid4()}",
                    "X-Request-Id": "d4-fail-enqueue-create",
                },
            )
        payload = created.json().get("data") or {}
        work_order_id = payload.get("id")
        evidence.append(
            f"enqueue_fail_create status={created.status_code} id={work_order_id} "
            f"wo_status={payload.get('status')}"
        )
        if created.status_code != 200 or not work_order_id:
            return False
        db_status = _wo_status(os.environ["TEST_DATABASE_URL"], int(work_order_id))
        evidence.append(f"enqueue_fail_db_status={db_status}")
        if db_status not in {"PENDING_ANALYSIS", "CREATED"}:
            ok = False
        listed = _list_failed()
        evidence.append(f"enqueue_fail_records={listed[:800] or '<empty>'}")
        record_id = ""
        needle = str(work_order_id)
        for line in listed.splitlines():
            if needle in line and ("ENQUEUE_FAILED" in line or "enqueue-" in line):
                record_id = line.split()[0]
                break
        if not record_id:
            evidence.append(
                "enqueue_fail_no_durable_record Redis+broker both unavailable "
                "would also lack evidence; compensation NOT recovery PASS"
            )
            return False
    finally:
        restore = _compose(["up", "-d", "--no-deps", "--force-recreate", "api-day4"])
        evidence.append(f"api_broker_restored={restore.returncode}")
    time.sleep(6)
    replay = subprocess.run(
        [
            "docker",
            "exec",
            WORKER,
            "python",
            "-m",
            "app.cli.replay_failed_task",
            "--record-id",
            record_id,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    evidence.append(f"enqueue_fail_replay rc={replay.returncode} stdout={(replay.stdout or '').strip()[:200]}")
    last = ""
    for _ in range(20):
        last = _wo_status(os.environ["TEST_DATABASE_URL"], int(work_order_id))
        if last in {"PENDING_ASSIGNMENT", "PENDING_DISPATCH", "ASSIGNED"}:
            break
        time.sleep(1.5)
    evidence.append(f"enqueue_fail_after_replay_status={last}")
    if last not in {"PENDING_ASSIGNMENT", "PENDING_DISPATCH", "ASSIGNED"}:
        ok = False
    return ok


def main() -> int:
    if not BASE:
        print("DAY4_FAILED_TASK_DRILL=NOT_RUN missing DAY4_BASE_URL")
        return 2
    if not os.environ.get("TEST_DATABASE_URL"):
        print("DAY4_FAILED_TASK_DRILL=NOT_RUN missing TEST_DATABASE_URL")
        return 2
    ids = _lookup_resident()
    evidence: list[str] = []
    failed = False
    last_status = ""
    listed = ""
    work_order_id: int | None = None
    try:
        recreate_fail = _compose(
            ["up", "-d", "--no-deps", "--force-recreate", "worker-day4"],
            {"LLM_PROVIDER_BEHAVIOR": "unavailable"},
        )
        evidence.append(f"worker_unavailable_recreate={recreate_fail.returncode}")
        if recreate_fail.returncode != 0:
            evidence.append((recreate_fail.stderr or "")[-300:])
            failed = True
        time.sleep(8)
        with httpx.Client(timeout=30.0) as client:
            resident = _token(client, ids["user_id"])
            created = client.post(
                f"{BASE}/api/v1/work-orders",
                json={
                    "house_id": ids["house_id"],
                    "text": "Controlled LLM unavailable drill",
                    "category": "ELECTRICAL",
                },
                headers={
                    "Authorization": f"Bearer {resident}",
                    "Idempotency-Key": f"d4-fail-analyze-{uuid4()}",
                    "X-Request-Id": "d4-fail-analyze-create",
                },
            )
            payload = created.json().get("data") or {}
            work_order_id = payload.get("id")
            err = created.json().get("error") or {}
            evidence.append(
                f"create_wo status={created.status_code} id={work_order_id} "
                f"wo_status={payload.get('status')} error={err.get('code')}"
            )
            if created.status_code != 200 or not work_order_id:
                failed = True
                print("\n".join(evidence))
                print("DAY4_FAILED_TASK_DRILL=FAIL")
                return 1
            time.sleep(25)
            listed = _list_failed()
            evidence.append(f"failed_list_after_analyze={listed[:800] or '<empty>'}")
            if not listed:
                failed = True
        recreate_ok = _compose(["up", "-d", "--no-deps", "--force-recreate", "worker-day4"])
        evidence.append(f"worker_restored={recreate_ok.returncode}")
        if recreate_ok.returncode != 0:
            failed = True
            evidence.append((recreate_ok.stderr or "")[-300:])
        time.sleep(8)
        if listed and work_order_id is not None:
            record_id = ""
            needle = str(work_order_id)
            for line in listed.splitlines():
                if "analyze_work_order_task" in line and needle in line:
                    record_id = line.split()[0]
                    break
            if not record_id:
                evidence.append(
                    f"replay_skipped_no_this_round_record work_order_id={work_order_id}"
                )
                failed = True
            else:
                replay = subprocess.run(
                    [
                        "docker",
                        "exec",
                        WORKER,
                        "python",
                        "-m",
                        "app.cli.replay_failed_task",
                        "--record-id",
                        record_id,
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                evidence.append(
                    f"replay record_id={record_id} rc={replay.returncode} "
                    f"stdout={(replay.stdout or '').strip()[:200]}"
                )
                if replay.returncode != 0:
                    failed = True
                db_url = os.environ["TEST_DATABASE_URL"]
                for _ in range(25):
                    last_status = _wo_status(db_url, int(work_order_id))
                    if last_status in {"PENDING_ASSIGNMENT", "PENDING_DISPATCH", "ASSIGNED"}:
                        break
                    time.sleep(1.5)
                evidence.append(f"after_replay_status={last_status}")
                if last_status not in {"PENDING_ASSIGNMENT", "PENDING_DISPATCH", "ASSIGNED"}:
                    failed = True
                listed_after = _list_failed()
                evidence.append(f"failed_list_after_replay={listed_after[:800] or '<empty>'}")
                engine = create_engine(db_url)
                with engine.connect() as connection:
                    analyses = int(
                        connection.execute(
                            text("SELECT COUNT(*) FROM work_order_analyses WHERE work_order_id=:id"),
                            {"id": int(work_order_id)},
                        ).scalar_one()
                    )
                engine.dispose()
                evidence.append(f"analysis_rows={analyses}")
                if analyses != 1:
                    failed = True
        else:
            failed = True
            evidence.append("replay_skipped_missing_record_or_work_order")
        enqueue_ok = _enqueue_fail_compensation(evidence, ids)
        if not enqueue_ok:
            failed = True
    finally:
        restore_worker = _compose(["up", "-d", "--no-deps", "--force-recreate", "worker-day4"])
        restore_api = _compose(["up", "-d", "--no-deps", "--force-recreate", "api-day4"])
        evidence.append(f"finally_worker_restored={restore_worker.returncode}")
        evidence.append(f"finally_api_restored={restore_api.returncode}")
        if restore_worker.returncode != 0 or restore_api.returncode != 0:
            failed = True
            evidence.append("finally_restore_failed")
    print("\n".join(evidence))
    if failed:
        print("DAY4_FAILED_TASK_DRILL=FAIL")
        return 1
    print("DAY4_FAILED_TASK_DRILL=PASS (Fake LLM unavailable then restored; not live vendor)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
