"""Verify serialized first-create behavior for two new SLA policy keys."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import sys
import threading
from urllib.parse import urlsplit

import httpx
from sqlalchemy import bindparam, text
from sqlalchemy.engine import make_url


REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"
OPS = REPO / "scripts" / "ops"
for candidate in (BACKEND, OPS):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from app.core.database import create_database_engine  # noqa: E402
from auth001_isolated_runtime import (  # noqa: E402
    ISOLATED_QUEUE,
    ISOLATED_VHOST,
    build_isolated_settings,
)


TASK_ID = "SLA-POLICY-MGMT-010"
API = "http://127.0.0.1:8083/api/v1"
PRIVATE_SOURCE = REPO / "tmp" / "sla-policy-management-010" / "context.json"
PRIVATE_SESSION = REPO / "tmp" / "sla-policy-management-010" / "first-create-session.json"
PRIVATE_RESULT = REPO / "tmp" / "sla-policy-management-010" / "first-create-context.json"
PUBLIC_EVIDENCE = Path(__file__).resolve().parent / "first-create-concurrency-evidence.json"


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _error_code(payload: dict[str, object]) -> str | None:
    error = payload.get("error")
    return error.get("code") if isinstance(error, dict) else None


def _guard(settings) -> None:
    database = make_url(settings.active_database_url)
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
        raise RuntimeError(f"AUTH001 target guard rejected settings: {actual}")


def _body(
    *,
    tenant_id: int,
    property_id: int,
    category: str,
    respond: int,
    complete: int,
) -> dict[str, object]:
    return {
        "tenant_id": tenant_id,
        "property_id": property_id,
        "category": category,
        "priority": "URGENT",
        "respond_minutes": respond,
        "complete_minutes": complete,
        "status": "ACTIVE",
        "expected_updated_at": None,
    }


def _parallel_put(
    *,
    run_id: str,
    token: str,
    label_prefix: str,
    bodies: tuple[dict[str, object], dict[str, object]],
) -> list[dict[str, object]]:
    barrier = threading.Barrier(2)

    def send(index: int, body: dict[str, object]) -> dict[str, object]:
        request_id = f"{run_id}-{label_prefix}-{index}"
        with httpx.Client(base_url=API, timeout=30.0) as client:
            barrier.wait(timeout=10)
            response = client.put(
                "/admin/sla-policies",
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-Request-Id": request_id,
                },
                json=body,
            )
            payload = response.json()
        return {
            "label": f"{label_prefix}-{index}",
            "request_id": request_id,
            "status": response.status_code,
            "error_code": _error_code(payload),
            "data": payload.get("data") if isinstance(payload.get("data"), dict) else None,
            "requested": {
                "respond_minutes": body["respond_minutes"],
                "complete_minutes": body["complete_minutes"],
            },
        }

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(send, index, body)
            for index, body in enumerate(bodies, start=1)
        ]
        return [future.result() for future in futures]


def _baseline_items(
    *,
    client: httpx.Client,
    token: str,
    tenant_id: int,
    property_id: int,
    request_id: str,
) -> httpx.Response:
    return client.get(
        "/admin/sla-policies",
        params={"property_id": property_id, "tenant_id": tenant_id},
        headers={
            "Authorization": f"Bearer {token}",
            "X-Request-Id": request_id,
        },
    )


def _refresh_session(
    *, client: httpx.Client, refresh_token: str, request_id: str
) -> dict[str, object]:
    response = client.post(
        "/auth/token/refresh",
        headers={"X-Request-Id": request_id},
        json={"refresh_token": refresh_token},
    )
    if response.status_code != 200:
        raise RuntimeError(f"Normal session refresh returned HTTP {response.status_code}")
    data = response.json().get("data")
    if not isinstance(data, dict) or not all(
        isinstance(data.get(key), str) and data[key]
        for key in ("access_token", "refresh_token")
    ):
        raise RuntimeError("Normal session refresh response is incomplete")
    return data


def main() -> int:
    if PUBLIC_EVIDENCE.exists() or PRIVATE_RESULT.exists():
        raise RuntimeError("First-create concurrency evidence already exists")
    source = json.loads(PRIVATE_SOURCE.read_text(encoding="utf-8"))
    if source.get("stage") != "complete":
        raise RuntimeError("SLA API private context is incomplete")
    settings = build_isolated_settings(enable_enqueue=True)
    _guard(settings)
    tenant_id = int(source["org"]["tenant_id"])
    property_id = int(source["org"]["property_id"])
    session = source["sessions"]["property_admin"]
    if PRIVATE_SESSION.exists():
        session = json.loads(PRIVATE_SESSION.read_text(encoding="utf-8"))
    token = str(session["access_token"])
    run_id = f"{source['run_id']}-first-create"

    with httpx.Client(base_url=API, timeout=30.0) as client:
        response = _baseline_items(
            client=client,
            token=token,
            tenant_id=tenant_id,
            property_id=property_id,
            request_id=f"{run_id}-baseline",
        )
        if response.status_code == 401:
            session = _refresh_session(
                client=client,
                refresh_token=str(session["refresh_token"]),
                request_id=f"{run_id}-refresh",
            )
            PRIVATE_SESSION.write_text(
                json.dumps(session, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            token = str(session["access_token"])
            response = _baseline_items(
                client=client,
                token=token,
                tenant_id=tenant_id,
                property_id=property_id,
                request_id=f"{run_id}-baseline-after-refresh",
            )
        if response.status_code != 200:
            raise RuntimeError(f"Baseline list returned HTTP {response.status_code}")
        items = response.json()["data"]["items"]
    occupied = {
        (item["category"], item["priority"], item["property_id"])
        for item in items
    }
    for category in ("CLEANING", "SECURITY"):
        if (category, "URGENT", property_id) in occupied:
            raise RuntimeError(f"Dedicated first-create key already exists: {category}/URGENT")

    same_body = _body(
        tenant_id=tenant_id,
        property_id=property_id,
        category="CLEANING",
        respond=22,
        complete=82,
    )
    same_results = _parallel_put(
        run_id=run_id,
        token=token,
        label_prefix="same-body",
        bodies=(same_body, dict(same_body)),
    )
    if sorted(int(item["status"]) for item in same_results) != [200, 200]:
        raise RuntimeError(f"Same-body first create did not return two successes: {same_results}")
    same_ids = {int(item["data"]["id"]) for item in same_results}
    if len(same_ids) != 1:
        raise RuntimeError("Same-body first create returned different policy IDs")

    different_bodies = (
        _body(
            tenant_id=tenant_id,
            property_id=property_id,
            category="SECURITY",
            respond=23,
            complete=83,
        ),
        _body(
            tenant_id=tenant_id,
            property_id=property_id,
            category="SECURITY",
            respond=24,
            complete=84,
        ),
    )
    different_results = _parallel_put(
        run_id=run_id,
        token=token,
        label_prefix="different-body",
        bodies=different_bodies,
    )
    if sorted(int(item["status"]) for item in different_results) != [200, 409]:
        raise RuntimeError(
            f"Different-body first create did not yield one success/one conflict: {different_results}"
        )
    loser = next(item for item in different_results if item["status"] == 409)
    if loser["error_code"] != "SLA.CONFLICT":
        raise RuntimeError("Different-body loser did not return SLA.CONFLICT")
    winner = next(item for item in different_results if item["status"] == 200)

    request_ids = [
        str(item["request_id"])
        for item in (*same_results, *different_results)
    ]
    policy_ids = [next(iter(same_ids)), int(winner["data"]["id"])]
    engine = create_database_engine(settings.active_database_url)
    try:
        with engine.connect() as connection:
            connection.execute(text("SET TRANSACTION READ ONLY"))
            revision = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
            policy_rows = connection.execute(
                text(
                    "SELECT id, tenant_id, property_id, category, priority, "
                    "respond_minutes, complete_minutes, status "
                    "FROM sla_policies WHERE id IN :policy_ids ORDER BY id"
                ).bindparams(bindparam("policy_ids", expanding=True)),
                {"policy_ids": policy_ids},
            ).mappings().all()
            audit_rows = connection.execute(
                text(
                    "SELECT id, actor_user_id, resource_id, request_id, details_json "
                    "FROM audit_logs WHERE action = 'SLA_POLICY_CHANGED' "
                    "AND request_id IN :request_ids ORDER BY id"
                ).bindparams(bindparam("request_ids", expanding=True)),
                {"request_ids": request_ids},
            ).mappings().all()
            connection.rollback()
    finally:
        engine.dispose()
    if revision != "20260919_0009" or len(policy_rows) != 2 or len(audit_rows) != 2:
        raise RuntimeError("First-create persistence/audit evidence is incomplete")
    audits_by_policy: dict[int, int] = {}
    audit_hashes: list[str] = []
    for row in audit_rows:
        policy_id = int(row["resource_id"])
        audits_by_policy[policy_id] = audits_by_policy.get(policy_id, 0) + 1
        audit_hashes.append(_hash(str(row["request_id"])))
    if audits_by_policy != {policy_ids[0]: 1, policy_ids[1]: 1}:
        raise RuntimeError(f"First-create audit cardinality is wrong: {audits_by_policy}")

    PRIVATE_RESULT.write_text(
        json.dumps(
            {
                "run_id": run_id,
                "same_results": same_results,
                "different_results": different_results,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    evidence = {
        "task_id": TASK_ID,
        "phase": "first_create_concurrency_api_and_database",
        "captured_at": datetime.now(UTC).isoformat(),
        "code_sha": "7814d45531a91efb6a63c63df261415fa446a1c4",
        "run_id": run_id,
        "environment": {
            "api": API,
            "database": "127.0.0.1:3307/property_ai_auth001_test",
            "revision": revision,
        },
        "checks": {
            "same_body_statuses": sorted(item["status"] for item in same_results),
            "same_body_same_policy_id": next(iter(same_ids)),
            "same_body_single_audit": True,
            "different_body_statuses": sorted(item["status"] for item in different_results),
            "different_body_loser_error": loser["error_code"],
            "different_body_winner": winner["requested"],
            "different_body_single_audit": True,
        },
        "policies": [dict(row) for row in policy_rows],
        "request_id_hashes": [
            _hash(str(item["request_id"]))
            for item in (*same_results, *different_results)
        ],
        "audit_request_id_hashes": audit_hashes,
        "boundaries": {
            "normal_session_reused": True,
            "seed_login_used": False,
            "direct_database_business_write": False,
            "database_verification_read_only": True,
            "migration_run": False,
            "production_claimed": False,
        },
        "private_context": "tmp/sla-policy-management-010/first-create-context.json",
        "redaction": "No token, phone, password, OTP, or full request ID is emitted.",
    }
    PUBLIC_EVIDENCE.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(evidence, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
