"""One fixed 32-request R3 diagnostic using the canonical JSON Schema contract."""

import argparse
from datetime import UTC, datetime
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import time

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).parent / "diagnostic-r3schema"
PRIVATE = ROOT / "tmp/real-ai-integration-027/diagnostic-r3schema"
BASE = ROOT / "reports/auth-identity-ai-001/local-qwen"
PROMPT_VERSION = "wo-analysis-v10-observed-event-r3fix2b"

spec = importlib.util.spec_from_file_location("r3fix2_candidate2_schema", Path(__file__).parent / "diagnostic-r3fix2-candidate2.py")
c2 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c2)
h = c2.h
c2.OUT = OUT
c2.TOTAL = OUT
c2.PRIVATE = PRIVATE
c2.c1.d.OUT = OUT
c2.c1.d.PRIVATE = PRIVATE
c2.c1.d.PROMPT_VERSION = PROMPT_VERSION
h.OUT = OUT
h.EXPECTED_PROMPT_VERSION = PROMPT_VERSION
h.__file__ = __file__
h.plans = c2.plans
h.prepare_images = c2.c1.d.images
h.raw_contract = c2.c1.d.contract

old_append = h.append_file


def append_file(path, value):
    if path.name == "reservation.json":
        value = dict(value)
        value.update(
            cumulative_qwen_limit=48,
            diagnostic_reserved_requests=32,
            business_reserved_requests=8,
            cumulative_window_seconds=5400,
            response_contract="canonical-json-schema",
        )
    old_append(path, value)


h.append_file = append_file


def _reservations():
    return sorted(OUT.glob("request-*/reservation.json"))


def remaining_seconds(_started, now):
    rows = _reservations()
    started = h.read(rows[0])["reserved_unix"] if rows else now
    return max(0.0, 5400 - (now - started))


h.remaining_seconds = remaining_seconds


def frozen():
    manifest = h.read(OUT / "frozen.json")
    root = Path(manifest["private_frozen_root"])
    for item in manifest["files"]:
        if h.sha(root / item["path"]) != item["sha256"] or h.sha(ROOT / item["path"]) != item["sha256"]:
            raise RuntimeError("FROZEN_SCHEMA_CANDIDATE_CHANGED")
    return root, manifest


h.frozen = frozen
c2.c1.d.frozen = frozen


def freeze():
    if OUT.exists() or PRIVATE.exists():
        raise RuntimeError("Existing schema diagnostic must be resumed, never reset")
    runtime = h.read(Path(__file__).parent / "r3schema-runtime.json")
    pid = int(runtime["pid"])
    command = (
        f"Get-CimInstance Win32_Process -Filter 'ProcessId={pid}' | "
        "Select-Object ProcessId,ExecutablePath,@{N='created';E={$_.CreationDate.ToString('o')}} | ConvertTo-Json -Compress"
    )
    actual = json.loads(subprocess.check_output(["powershell", "-NoProfile", "-Command", command], text=True))
    if actual["ExecutablePath"] != runtime["executable"]:
        raise RuntimeError("MODEL_RUNTIME_IDENTITY_CHANGED")

    target = PRIVATE / "frozen"
    target.mkdir(parents=True)
    names = [item["path"] for item in h.read(BASE / "crossfield-diagnostic-01/frozen-inputs-v2.json")["files"]]
    names += [
        "backend/app/domain/observed_event.py",
        "backend/app/provider/llm/__init__.py",
        "backend/app/service/analysis.py",
        "scripts/ops/work_order_response_format.json",
        "reports/real-ai-integration-027/diagnostic-r3schema.py",
        "docs/05-AI-DISPATCH-SPEC.md",
    ]
    entries = []
    for name in sorted(set(names)):
        destination = target / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, destination)
        entries.append({"path": name, "sha256": h.sha(destination)})

    OUT.mkdir(parents=True)
    append_file(OUT / "frozen.json", {
        "base_sha": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "private_frozen_root": str(target),
        "files": entries,
        "model": h.MODEL,
        "model_digest": h.DIGEST,
        "prompt_version": PROMPT_VERSION,
        "parameters": {"context_length": 4096, "temperature": 0, "thinking": False, "llm_max_tokens": 512, "vlm_max_tokens": 256, "per_request_seconds": 60},
        "contract_change": "OpenAI-compatible strict json_schema generated from ObservedAnalysisOutput; semantic validators unchanged",
        "fixed_set_requests": 32,
        "cumulative_batch_budget": {"qwen": 48, "asr": 1, "seconds": 5400, "business_qwen_reserved": 8},
    })
    runtime.update(verified_at_utc=datetime.now(UTC).isoformat(), inference_calls_this_check=0)
    append_file(OUT / "runtime.json", runtime)
    append_file(OUT / "images.json", h.read(BASE / "crossfield-diagnostic-01/images.json"))
    h.prepare_images()
    print("R3 JSON SCHEMA CANDIDATE FROZEN; ZERO INFERENCE")


def collect_one():
    before = len(_reservations())
    h.execute_next()
    after = len(_reservations())
    if after == before:
        raise RuntimeError("NO_RESERVATION_RESOURCE_OR_GUARD_STOP")
    folder = OUT / f"request-{after:02d}"
    if (OUT / "stop.json").exists():
        raise RuntimeError("STRUCTURAL_PROVIDER_OR_RESOURCE_STOP")
    old_append(folder / "judgement.json", {
        "continue": True,
        "response_sha256": h.sha(folder / "response.json"),
        "semantic_status": "PENDING_FINAL_FIXED_SET_ASSESSMENT",
        "note": "Collection only; this is not an individual semantic PASS.",
    })


def collect_all():
    while len(_reservations()) < 32:
        collect_one()
    rows = [h.read(path) for path in _reservations()]
    old_append(OUT / "collection-complete.json", {
        "status": "FIXED_SET_COLLECTED_PENDING_SEMANTIC_ASSESSMENT",
        "fixed_requests": len(rows),
        "first_reserved_unix": rows[0]["reserved_unix"],
        "last_reserved_unix": rows[-1]["reserved_unix"],
        "elapsed_seconds": rows[-1]["reserved_unix"] - rows[0]["reserved_unix"],
    })
    print("FIXED SET COLLECTED")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["freeze", "next", "collect-all", "child"])
    parser.add_argument("--number", type=int)
    args = parser.parse_args()
    if args.mode == "freeze": freeze()
    elif args.mode == "child": h.child(args.number)
    elif args.mode == "next": collect_one()
    else: collect_all()
