"""Create one bounded synthetic Incident through the normal API and task services.

Only the five work orders recorded in the private resume file are eligible.  The
running publisher is deliberately not used: each newly persisted 0012 intent is
claimed and executed synchronously with the deterministic Development provider.
This is prerequisite data for the Incident disposition UI, not real-Qwen proof.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import site
import sys

ROOT = Path(__file__).resolve().parents[2]
REPORT_DIR = Path(__file__).resolve().parent
PRIVATE_SOURCE = ROOT / "tmp/resident-wo-presentation-020/context.json"
PRIVATE_DIR = ROOT / "tmp/production-v1-closeout/incident-disposition"
PRIVATE_STATE = PRIVATE_DIR / "context.json"
RUN_LOCK = PRIVATE_DIR / "runner.lock"
PUBLIC = REPORT_DIR / "runtime-closeout/incident-prerequisite.json"
API = "http://127.0.0.1:8083/api/v1"
OBJECT_LIMIT = 5
PROTECTED_WORK_ORDERS = (12, 24, 25, 30, 34)

site.addsitedir(str(ROOT / ".venv/Lib/site-packages"))

import httpx


def _load_runtime():
    path = REPORT_DIR / "closeout-runtime.py"
    spec = importlib.util.spec_from_file_location("closeout_runtime_fixed", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("fixed closeout runtime could not be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


runtime = _load_runtime()
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.database import assert_isolated_auth_database, create_database_engine
from app.model.ai import Incident, IncidentWorkOrder
from app.model.task_intent import TaskIntent
from app.model.work_order import WorkOrder, WorkOrderAssignment
from app.service.task_intent import ANALYZE
from app.worker.task_intents import execute_intent


def _challenge_answer(prompt: str) -> str:
    match = re.fullmatch(r"\s*(\d+)\s*\+\s*(\d+)\s*=\s*\?\s*", prompt)
    if match is None:
        raise RuntimeError("unsupported visual challenge")
    return str(int(match.group(1)) + int(match.group(2)))


class Client:
    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self.http = httpx.Client(base_url=API, timeout=20.0)
        self.calls: list[dict[str, object]] = []

    def call(self, method: str, path: str, *, label: str, token: str | None = None,
             body: dict | None = None, headers: dict | None = None,
             expected: tuple[int, ...] = (200,)) -> tuple[int, dict]:
        request_id = f"{self.run_id}-{label}"
        request_headers = {"X-Request-Id": request_id, **(headers or {})}
        if token:
            request_headers["Authorization"] = f"Bearer {token}"
        response = self.http.request(method, path, headers=request_headers, json=body)
        payload = response.json()
        self.calls.append({"label": label, "method": method, "path": path,
                           "status": response.status_code})
        if response.status_code not in expected:
            error = payload.get("error") if isinstance(payload, dict) else None
            code = error.get("code") if isinstance(error, dict) else None
            raise RuntimeError(f"{label}: HTTP {response.status_code} code={code}")
        return response.status_code, payload

    def data(self, *args, **kwargs) -> dict:
        _status, payload = self.call(*args, **kwargs)
        data = payload.get("data")
        if not isinstance(data, dict):
            raise RuntimeError("API response has no data object")
        return data

    def login(self, phone: str, label: str) -> str:
        challenge = self.data("POST", "/auth/challenge", label=f"{label}-challenge",
                              body={"phone": phone, "purpose": "login"})
        self.data("POST", "/auth/sms/send", label=f"{label}-send",
                         body={"phone": phone, "purpose": "login",
                               "challenge_id": challenge["challenge_id"],
                               "challenge_answer": _challenge_answer(challenge["prompt"])})
        inbox = self.data("POST", "/auth/sms/test-inbox", label=f"{label}-inbox",
                          body={"phone": phone, "purpose": "login"})
        session = self.data("POST", "/auth/sms/login", label=f"{label}-verify",
                            body={"phone": phone, "code": inbox["code"]})
        return str(session["access_token"])


def _save_private(value: dict) -> None:
    PRIVATE_DIR.mkdir(parents=True, exist_ok=True)
    temporary = PRIVATE_STATE.with_suffix(".json.next")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, PRIVATE_STATE)


@contextmanager
def _exclusive_run():
    PRIVATE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(RUN_LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise RuntimeError(
            "Incident prerequisite runner already owns this run or needs manual stale-lock review"
        ) from exc
    try:
        os.write(descriptor, f"pid={os.getpid()}\n".encode("ascii"))
    finally:
        os.close(descriptor)
    try:
        yield
    finally:
        # Only this process can own this exact O_EXCL lock. A hard process exit
        # leaves it behind and therefore fails closed on the next invocation.
        RUN_LOCK.unlink(missing_ok=True)


def _settings():
    base = runtime.settings()
    values = base.model_dump()
    values.update(llm_provider="development", vlm_provider="development",
                  asr_provider="development", llm_provider_behavior="success",
                  ai_auto_assign=True, durable_tasks_enabled=True)
    candidate = type(base)(_env_file=None, **values)
    assert_isolated_auth_database(candidate, database_url=candidate.active_database_url)
    if candidate.incident_threshold != OBJECT_LIMIT:
        raise RuntimeError("configured incident threshold is outside this bounded fixture")
    return candidate


def _member_snapshot(session: Session, ids: list[int]) -> list[dict[str, object]]:
    result = []
    for work_order_id in ids:
        work = session.get(WorkOrder, work_order_id)
        result.append({
            "work_order_id": work_order_id,
            "status": None if work is None else work.status,
            "version": None if work is None else work.version,
            "current_assignment_id": None if work is None else work.current_assignment_id,
            "assignment_count": session.scalar(select(func.count()).select_from(WorkOrderAssignment)
                                                .where(WorkOrderAssignment.work_order_id == work_order_id)),
        })
    return result


def _run_locked() -> None:
    source = json.loads(PRIVATE_SOURCE.read_text(encoding="utf-8"))
    settings = _settings()
    engine = create_database_engine(settings.active_database_url)
    try:
        with engine.connect() as connection:
            revision = connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one()
        if revision != "20260925_0012_task_intents":
            raise RuntimeError("AUTH001 is not at approved revision 0012")

        state = (json.loads(PRIVATE_STATE.read_text(encoding="utf-8"))
                 if PRIVATE_STATE.exists() else {
                     "run_id": f"incident-closeout-{secrets.token_hex(5)}",
                     "work_order_ids": [], "request_ids": [], "intent_ids": [],
                     "pending_slot": None,
                 })
        run_id = str(state["run_id"])
        if not run_id.startswith("incident-closeout-"):
            raise RuntimeError("private run marker is outside this fixture")
        state.setdefault("pending_slot", None)
        # Persist the immutable run marker before login/session or business
        # writes.  A crash after a POST must reuse the same slot and
        # idempotency key; it must never create a fresh run namespace.
        _save_private(state)
        client = Client(run_id)
        client.call("GET", "/readyz", label="ready")
        resident_token = client.login(str(source["phones"]["resident"]), "resident")
        manager_token = client.login(str(source["phones"]["manager"]), "manager")
        org = {key: source[key] for key in ("tenant_id", "property_id", "house_id")}

        while len(state["work_order_ids"]) < OBJECT_LIMIT:
            slot = state.get("pending_slot")
            if slot is None:
                index = len(state["work_order_ids"]) + 1
                slot = {
                    "index": index,
                    "request_id": f"{run_id}-create-{index}",
                    "idempotency_key": f"{run_id}-order-{index}",
                    "work_order_id": None,
                    "intent_id": None,
                }
                state["pending_slot"] = slot
                _save_private(state)
            index = int(slot["index"])
            if index != len(state["work_order_ids"]) + 1 or not 1 <= index <= OBJECT_LIMIT:
                raise RuntimeError("private slot sequence or object limit is invalid")
            if slot.get("work_order_id") is None:
                _status, payload = client.call(
                    "POST", "/work-orders", token=resident_token, label=f"create-{index}",
                    headers={"Idempotency-Key": str(slot["idempotency_key"])},
                    body={"house_id": int(org["house_id"]),
                          "tenant_id": int(org["tenant_id"]),
                          "property_id": int(org["property_id"]),
                          "contact_phone": str(source["phones"]["resident"]),
                          "text": f"{run_id} 第{index}户报告同楼公共区域插座持续冒火花"},
                )
                slot["work_order_id"] = int(payload["data"]["id"])
                _save_private(state)
            work_order_id = int(slot["work_order_id"])
            with Session(engine, expire_on_commit=False) as session:
                intent = session.scalar(select(TaskIntent).where(
                    TaskIntent.operation == ANALYZE,
                    TaskIntent.resource_id == work_order_id,
                    TaskIntent.request_id == str(slot["request_id"]),
                ))
                if intent is None or intent.publish_attempt != 0:
                    raise RuntimeError("new task intent was published or is missing")
                intent_id, task_id = intent.id, intent.task_id
                intent_status, execution_attempt = intent.status, intent.execution_attempt
            if intent_status == "PENDING" and execution_attempt == 0:
                outcome = execute_intent(engine, settings, intent_id, task_id)
                if outcome != "SUCCEEDED":
                    raise RuntimeError(f"Development intent {intent_id} ended as {outcome}")
            elif intent_status != "SUCCEEDED":
                # PROCESSING/PUBLISHED/RETRY_WAIT and any unknown state need a
                # read-only reconciliation decision; never advance a new slot.
                raise RuntimeError(f"task intent {intent_id} has unresolved state {intent_status}")
            slot["intent_id"] = intent_id
            state["work_order_ids"].append(work_order_id)
            state["request_ids"].append(str(slot["request_id"]))
            state["intent_ids"].append(intent_id)
            state["pending_slot"] = None
            _save_private(state)

        ids = [int(value) for value in state["work_order_ids"]]
        if len(ids) != OBJECT_LIMIT or len(set(ids)) != OBJECT_LIMIT:
            raise RuntimeError("bounded fixture object count changed")
        with Session(engine, expire_on_commit=False) as session:
            works = list(session.scalars(select(WorkOrder).where(WorkOrder.id.in_(ids)).order_by(WorkOrder.id)))
            incident_ids = {item.incident_id for item in works if item.incident_id is not None}
            if len(works) != OBJECT_LIMIT or len(incident_ids) != 1:
                raise RuntimeError("the bounded work orders did not form exactly one Incident")
            incident_id = int(next(iter(incident_ids)))
            incident = session.get(Incident, incident_id)
            links = list(session.scalars(select(IncidentWorkOrder).where(
                IncidentWorkOrder.incident_id == incident_id).order_by(IncidentWorkOrder.work_order_id)))
            if incident is None or incident.status != "OPEN" or {item.work_order_id for item in links} != set(ids):
                raise RuntimeError("Incident membership or initial state is unexpected")
            before = _member_snapshot(session, ids)
            protected = _member_snapshot(session, list(PROTECTED_WORK_ORDERS))
            updated_at = incident.updated_at.isoformat()

        # Failure and permission paths use the actual API before the page changes state.
        client.call("POST", f"/supervisor/incidents/{incident_id}/status",
                    token=manager_token, label="blank-reason",
                    body={"status": "MONITORING", "reason": " ",
                          "expected_updated_at": updated_at}, expected=(422,))
        client.call("POST", f"/supervisor/incidents/{incident_id}/status",
                    token=manager_token, label="illegal-open-close",
                    body={"status": "CLOSED", "reason": "跳过解决阶段",
                          "expected_updated_at": updated_at}, expected=(409,))
        client.call("GET", f"/supervisor/incidents/{incident_id}",
                    token=resident_token, label="resident-detail-denied", expected=(403,))
        client.call("POST", f"/supervisor/incidents/{incident_id}/status",
                    token=resident_token, label="resident-update-denied",
                    body={"status": "MONITORING", "reason": "无权操作",
                          "expected_updated_at": updated_at}, expected=(403,))

        state.update(incident_id=incident_id, initial_updated_at=updated_at,
                     member_before=before, protected_before=protected)
        _save_private(state)
        PUBLIC.parent.mkdir(parents=True, exist_ok=True)
        public = {
            "task": "Production V1 closeout Incident disposition prerequisite",
            "captured_at": datetime.now(UTC).isoformat(),
            "source_commit": "fe90913a6f3918bca33b2f100137a786a67c4f86",
            "run_id": run_id,
            "environment": {"api": "127.0.0.1:8083", "database": "AUTH001@0012",
                            "provider": "DEVELOPMENT deterministic fixture only",
                            "real_qwen_calls": 0, "queue_messages_sent": 0},
            "objects": {"work_order_ids": ids, "intent_ids": state["intent_ids"],
                        "incident_id": incident_id, "incident_status": "OPEN",
                        "member_count": len(ids)},
            "member_before": before,
            "protected_work_orders_snapshot": protected,
            "api_checks": client.calls,
            "negative_results": {"blank_reason": 422, "illegal_open_to_closed": 409,
                                 "resident_detail": 403, "resident_update": 403},
            "private_contact_or_tokens_in_public_evidence": False,
        }
        PUBLIC.write_text(json.dumps(public, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"run_id": run_id, "work_orders": ids,
                          "incident_id": incident_id, "result": "READY_FOR_PAGE"}))
    finally:
        engine.dispose()


def main() -> None:
    if PUBLIC.exists():
        raise RuntimeError("public prerequisite evidence already exists; do not duplicate objects")
    with _exclusive_run():
        _run_locked()


if __name__ == "__main__":
    main()
