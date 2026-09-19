"""Official-session AUTH001 create/replay smoke; emits redacted JSON only."""

from __future__ import annotations

import json
import re
import subprocess
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4


REPO = Path(__file__).resolve().parents[2]
BASE = "http://127.0.0.1:8083/api/v1"
ACCOUNTS_PATH = REPO / "reports" / "mp-current-full-regression" / "accounts.local.json"
PUBLIC_PATH = REPO / "reports" / "mp-current-full-regression" / "accounts-public.json"
WORKER_LOG_DIR = REPO / "tmp" / "auth001-runtime"


def _request(
    path: str,
    payload: dict[str, object] | None,
    *,
    request_id: str,
    token: str | None = None,
    idempotency_key: str | None = None,
) -> tuple[int, dict[str, object]]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {
        "Accept": "application/json",
        "X-Request-Id": request_id,
    }
    if body is not None:
        headers["Content-Type"] = "application/json"
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    if idempotency_key is not None:
        headers["Idempotency-Key"] = idempotency_key
    request = urllib.request.Request(
        f"{BASE}{path}",
        data=body,
        headers=headers,
        method="GET" if payload is None else "POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        response_body = json.loads(exc.read().decode("utf-8"))
        raise RuntimeError(
            f"{path} returned HTTP {exc.code}: "
            f"{response_body.get('error', {}).get('code', 'UNKNOWN')}"
        ) from exc


def _challenge_answer(prompt: str) -> str:
    match = re.fullmatch(r"(\d+)\s*\+\s*(\d+)\s*=\s*\?", prompt.strip())
    if match is None:
        raise RuntimeError("Development challenge format was not recognized")
    return str(int(match.group(1)) + int(match.group(2)))


def _latest_worker_log() -> Path:
    candidates = tuple(WORKER_LOG_DIR.glob("worker-*.out.log"))
    if not candidates:
        raise RuntimeError("No isolated worker log was found")
    return max(candidates, key=lambda item: item.stat().st_ctime_ns)


def _login(phone: str, run_id: str) -> tuple[str, str]:
    _, challenge = _request(
        "/auth/challenge",
        {"phone": phone, "purpose": "login"},
        request_id=f"{run_id}-challenge",
    )
    challenge_data = challenge["data"]
    _, sent = _request(
        "/auth/sms/send",
        {
            "phone": phone,
            "purpose": "login",
            "challenge_id": challenge_data["challenge_id"],
            "challenge_answer": _challenge_answer(challenge_data["prompt"]),
        },
        request_id=f"{run_id}-sms-send",
    )
    _, inbox = _request(
        "/auth/sms/test-inbox",
        {"phone": phone, "purpose": "login"},
        request_id=f"{run_id}-sms-inbox",
    )
    _, login = _request(
        "/auth/sms/login",
        {"phone": phone, "code": inbox["data"]["code"]},
        request_id=f"{run_id}-login",
    )
    return login["data"]["access_token"], sent["data"]["provider_mode"]


def main() -> int:
    accounts = json.loads(ACCOUNTS_PATH.read_text(encoding="utf-8"))
    public = json.loads(PUBLIC_PATH.read_text(encoding="utf-8"))
    run_id = f"committed-auto-{uuid4().hex[:12]}"
    idempotency_key = f"{run_id}-one-order"
    worker_log = _latest_worker_log()
    worker_before = worker_log.read_text(encoding="utf-8", errors="replace")
    token, sms_mode = _login(accounts["phones"]["resident"], run_id)
    payload = {
        "house_id": public["org"]["house_id"],
        "text": "提交后自动分析并由规则派给在岗维修人员。",
    }
    create_status, created = _request(
        "/work-orders",
        payload,
        request_id=f"{run_id}-create",
        token=token,
        idempotency_key=idempotency_key,
    )
    replay_status, replayed = _request(
        "/work-orders",
        payload,
        request_id=f"{run_id}-replay",
        token=token,
        idempotency_key=idempotency_key,
    )
    work_order_id = int(created["data"]["id"])
    final_detail: dict[str, object] | None = None
    for _attempt in range(60):
        _, detail = _request(
            f"/work-orders/{work_order_id}",
            None,
            request_id=f"{run_id}-readback",
            token=token,
        )
        final_detail = detail["data"]
        if final_detail["work_order"]["status"] not in {
            "PENDING_ANALYSIS",
            "PENDING_ASSIGNMENT",
        }:
            break
        time.sleep(0.25)
    time.sleep(1)
    worker_delta = worker_log.read_text(encoding="utf-8", errors="replace")[
        len(worker_before) :
    ]
    task_received_count = len(
        re.findall(
            r'"event": "Task app\.worker\.tasks\.analyze_work_order_task\[[^]]+\] received"',
            worker_delta,
        )
    )
    assignments = [] if final_detail is None else final_detail.get("assignments", [])
    logs = [] if final_detail is None else final_detail.get("logs", [])
    result = {
        "task_id": "AUTO-DISPATCH-MAINLINE-001-COMMITTED-SMOKE",
        "captured_at": datetime.now(UTC).isoformat(),
        "head": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=REPO, text=True
        ).strip(),
        "implementation_commit": "d144bcddcfc24e6e9eaf60509acd124711bc0561",
        "environment": {
            "api": BASE,
            "database": "127.0.0.1:3307/property_ai_auth001_test",
            "revision": "20260919_0009",
            "queue": "property_ai.auth001",
            "worker_log": worker_log.name,
            "sms_provider_mode": sms_mode,
        },
        "create_status": create_status,
        "replay_status": replay_status,
        "work_order_id": work_order_id,
        "replay_returned_same_id": replayed["data"]["id"] == work_order_id,
        "analysis_task_received_count": task_received_count,
        "final_status": None
        if final_detail is None
        else final_detail["work_order"]["status"],
        "assignment_reason": None if not assignments else assignments[-1]["reason"],
        "assignment_actor_is_system": bool(
            assignments and assignments[-1]["assigned_by_user_id"] is None
        ),
        "log_events": [item["event_code"] for item in logs],
        "boundaries": {
            "official_sms_session": True,
            "same_idempotency_key_replayed": True,
            "manual_analysis_or_assignment_used": False,
            "direct_database_write_used": False,
            "real_model_verified": False,
            "production_v1_passed": False,
        },
        "redaction": "No phone, SMS code, access/refresh token, credential, resident name, or work-order text is emitted.",
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    passed = (
        create_status == 200
        and replay_status == 200
        and result["replay_returned_same_id"]
        and task_received_count == 1
        and result["final_status"] == "ASSIGNED"
        and result["assignment_reason"] == "AUTO_DISPATCH"
        and result["assignment_actor_is_system"]
    )
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
