"""Sanitized existing-process/log metadata; no business requests or messages."""
from pathlib import Path
from datetime import UTC, datetime
import hashlib
import json
import platform
import subprocess
import sys

import app.api.v1.work_orders
import app.service.work_order

REPO = Path(__file__).resolve().parents[2]
OUT = Path(__file__).parent
runtime = REPO / "tmp/auth001-runtime"
def command(args):
    completed = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
    if completed.returncode:
        raise RuntimeError(f"metadata command failed: {args[0]} status={completed.returncode}")
    return completed.stdout.strip()

ps = "Get-CimInstance Win32_Process | Where-Object { $_.ProcessId -in 39184,3364,29484 } | Select-Object ProcessId,ParentProcessId,ExecutablePath,CommandLine | ConvertTo-Json"
processes = json.loads(command(["powershell", "-NoProfile", "-Command", ps]))
worker_rows = []
for line in (runtime / "worker-65f20c5-wo014.stdout.log").read_text(encoding="utf-8-sig").splitlines():
    if not line.startswith("{"):
        continue
    row = json.loads(line)
    if row.get("event") not in {"auto_dispatch_assigned", "auto_dispatch_outcome"}:
        continue
    worker_rows.append({key:row[key] for key in ("timestamp","event","service","operation","task_id","work_order_id","employee_id","assignment_id","analysis_id","outcome") if key in row})
assert {row["work_order_id"] for row in worker_rows} == {1,2,3,4}
api_source = runtime / "api-65f20c5-wo014.stdout.log"
source = runtime / "worker-65f20c5-wo014.stdout.log"
facts = {
    "task_id":"WO-REOPEN-RECEIPT-HISTORY-014", "run_id":"wo014-745f67277104",
    "collected_at_utc":datetime.now(UTC).isoformat(),
    "product_sha":command(["git","rev-parse","HEAD"]),
    "interpreter":sys.executable, "python_version":platform.python_version(), "architecture":platform.machine(),
    "dependency_reuse":"-I entry; explicit project .venv/Lib/site-packages only; Worker child PYTHONNOUSERSITE=1 and exact package/backend PYTHONPATH via existing recovered launcher",
    "collector_module_sources":{"api":app.api.v1.work_orders.__file__,"service":app.service.work_order.__file__},
    "code_binding":"Main committed 65f20c5 before starting both listed processes. No product edits afterward; entrypoint paths/log headers bind launch. Module paths above are collector imports, not debugger introspection into running processes.",
    "actual_processes":processes,
    "actual_api_startup":api_source.read_text(encoding="utf-8-sig").splitlines()[0],
    "actual_worker_startup":source.read_text(encoding="utf-8-sig").splitlines()[:2],
    "worker_auto_dispatch_events":worker_rows,
    "project_config_sha256":hashlib.sha256((REPO/"miniprogram/project.config.json").read_bytes()).hexdigest().upper(),
    "recovery":{"container_id":"fa78177adc270834bd1c25e63cb86b1d6a7fd3f7cfb2faa66c5ac4f67ab0d66f","image":"mysql:8.0.41","data_mount":"/var/lib/mysql tmpfs","host_port":3307,
        "prior_auth001_schema_count":0,"prior_auth001_objects":"lost; historical version-bound evidence retained, never current objects",
        "authorizations":"User separately approved Desktop startup, existing mysql-test startup/read-only check, then AUTH001-only 0007->0009 restoration and fresh synthetic data on 2026-09-23.",
        "desktop_side_effect":"Existing restart policies automatically started existing shared/Day4/prodlike containers. Main did not manually operate them or inspect/consume their messages.",
        "provision":"Existing guarded scripts/ops/provision_auth001_isolated_db.py via retained recovered launcher; 6 isolated auth tests passed, no general destructive fixture activated; no 0010, other DB migration or container recreation."},
    "known_evidence_helper_interruptions":[
        "Fixture manager grant committed before JSON serialization failed. Resumed by reading exact existing grant, not granting twice. Prior completed API steps reused from private checkpoint.",
        "API evidence script initially imported unavailable Pillow and stopped before any business request. Replaced with standard-library synthetic PNG encoding, no dependency install.",
        "Direct status OFF_DUTY request was correctly rejected 422; used normal checkout to make employee ineligible, then normal checkin after test. Prior successful steps not repeated.",
    ],
}
(OUT / "runtime-environment.json").write_text(json.dumps(facts,ensure_ascii=False,indent=2),encoding="utf-8")
print(json.dumps({"product_sha":facts["product_sha"],"processes":[p["ProcessId"] for p in processes],"worker_events":len(worker_rows)}))
