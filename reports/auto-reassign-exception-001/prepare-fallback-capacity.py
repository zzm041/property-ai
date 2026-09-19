"""Use official synthetic admin APIs to make the fallback employee eligible."""

from __future__ import annotations

import importlib.util
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4


HERE = Path(__file__).resolve().parent
PREPARE_SCRIPT = HERE / "prepare-runtime-evidence.py"
ACCOUNTS_PATH = HERE.parent / "mp-current-full-regression" / "accounts.local.json"


def main() -> int:
    spec = importlib.util.spec_from_file_location("reassign_prepare", PREPARE_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load the existing isolated fixture helper")
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)

    accounts = json.loads(ACCOUNTS_PATH.read_text(encoding="utf-8"))
    run_id = f"auto-reassign-capacity-{uuid4().hex[:10]}"
    token = helper._login(accounts["phones"]["tenant_admin"], f"{run_id}-login")
    _, before_response = helper._request(
        "GET",
        "/admin/employees/4",
        request_id=f"{run_id}-before",
        token=token,
    )
    before = before_response["data"]["employee"]
    target_capacity = max(int(before["max_capacity"]), 8)
    _, after_response = helper._request(
        "PATCH",
        "/admin/employees/4",
        request_id=f"{run_id}-update",
        token=token,
        payload={"max_capacity": target_capacity},
    )
    after = after_response["data"]
    evidence = {
        "task_id": "AUTO-REASSIGN-EXCEPTION-001-FIXTURE",
        "captured_at": datetime.now(UTC).isoformat(),
        "employee_id": 4,
        "previous_max_capacity": before["max_capacity"],
        "current_max_capacity": after["max_capacity"],
        "method": "official tenant-admin employee PATCH API",
        "reason": "Synthetic fallback employee had current_load equal to max_capacity; preserve no-eligible evidence, then enable the approved redispatch path.",
        "direct_database_write_used": False,
        "redaction": "No phone, SMS code, token, password, employee name, or credential is stored.",
    }
    (HERE / "fallback-capacity-evidence.json").write_text(
        f"{json.dumps(evidence, ensure_ascii=False, indent=2)}\n",
        encoding="utf-8",
    )
    print(json.dumps(evidence, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
