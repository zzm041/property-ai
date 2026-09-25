"""Approved fixed R3fix2b diagnostic under the low-confidence server policy.

This is not a new prompt candidate.  Two byte-identical model responses from
candidate-02 are reused with explicit provenance; remaining fixed requests are
collected once.  Semantic acceptance is assessed only after the fixed set is
complete, while malformed/provider/resource failures still fail closed.
"""
from __future__ import annotations

import argparse
from datetime import UTC, datetime
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import time

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).parent / "diagnostic-r3policy"
PRIVATE = ROOT / "tmp" / "real-ai-integration-027" / "diagnostic-r3policy"
BASE = ROOT / "reports" / "auth-identity-ai-001" / "local-qwen"
PREVIOUS = Path(__file__).parent / "diagnostic-r3fix2" / "candidate-02"
PROMPT_VERSION = "wo-analysis-v10-observed-event-r3fix2b"

spec = importlib.util.spec_from_file_location(
    "r3fix2_candidate2", Path(__file__).parent / "diagnostic-r3fix2-candidate2.py"
)
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
h.prepare_images = c2.c1.d.images
h.raw_contract = c2.c1.d.contract


def plans():
    return c2.plans()


h.plans = plans


def frozen():
    manifest = h.read(OUT / "frozen.json")
    root = Path(manifest["private_frozen_root"])
    for item in manifest["files"]:
        if h.sha(root / item["path"]) != item["sha256"] or h.sha(ROOT / item["path"]) != item["sha256"]:
            raise RuntimeError("FROZEN_POLICY_CANDIDATE_CHANGED")
    return root, manifest


h.frozen = frozen
c2.c1.d.frozen = frozen


def _reservations():
    return sorted(OUT.glob("request-*/reservation.json"))


def remaining_seconds(_started, now):
    actual = [h.read(path) for path in _reservations() if h.read(path).get("new_inference")]
    if not actual:
        return 2400.0
    return max(0.0, 2400 - (now - actual[0]["reserved_unix"]))


h.remaining_seconds = remaining_seconds

old_append = h.append_file


def append_file(path, value):
    if path.name == "reservation.json":
        value = dict(value)
        value.update(
            new_inference=True,
            authorized_new_request_limit=32,
            wall_seconds=2400,
            prompt_candidate="unchanged-r3fix2b",
        )
    old_append(path, value)


h.append_file = append_file


def freeze():
    if OUT.exists() or PRIVATE.exists():
        raise RuntimeError("existing policy diagnostic must be resumed, never reset")
    target = PRIVATE / "frozen"
    target.mkdir(parents=True)
    names = [item["path"] for item in h.read(BASE / "crossfield-diagnostic-01/frozen-inputs-v2.json")["files"]]
    names += [
        "backend/app/domain/ai.py",
        "backend/app/domain/audit.py",
        "backend/app/domain/observed_event.py",
        "backend/app/service/analysis.py",
        "backend/app/service/auto_dispatch.py",
        "backend/app/service/work_order.py",
        "backend/app/api/v1/work_orders.py",
        "backend/app/schema/work_order.py",
        "miniprogram/utils/work-order-display.js",
        "miniprogram/pages/work-orders/detail.js",
        "miniprogram/pages/work-orders/detail.wxml",
        "docs/05-AI-DISPATCH-SPEC.md",
        "reports/real-ai-integration-027/diagnostic-r3policy.py",
    ]
    entries = []
    for name in sorted(set(names)):
        destination = target / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, destination)
        entries.append({"path": name, "sha256": h.sha(destination)})

    runtime = h.read(BASE / "crossfield-diagnostic-01/runtime.json")
    pid = int(runtime["pid"])
    command = (
        f"Get-CimInstance Win32_Process -Filter 'ProcessId={pid}' | "
        "Select-Object ProcessId,ExecutablePath,@{N='created';E={$_.CreationDate.ToString('o')}} | ConvertTo-Json -Compress"
    )
    actual = json.loads(subprocess.check_output(["powershell", "-NoProfile", "-Command", command], text=True))
    if actual["ExecutablePath"] != runtime["executable"] or datetime.fromisoformat(actual["created"]) != datetime.fromisoformat(runtime["process_created_at"]):
        raise RuntimeError("MODEL_RUNTIME_IDENTITY_CHANGED")

    OUT.mkdir(parents=True)
    old_append(OUT / "frozen.json", {
        "base_sha": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "private_frozen_root": str(target),
        "files": entries,
        "model": h.MODEL,
        "model_digest": h.DIGEST,
        "prompt_version": PROMPT_VERSION,
        "prompt_candidate_changed": False,
        "parameters": {"context_length": 4096, "temperature": 0, "thinking": False,
                       "llm_max_tokens": 512, "vlm_max_tokens": 256, "per_request_seconds": 60},
        "fixed_set": "24 text + 4 image pairs = 32 model requests",
        "new_budget": {"maximum_requests": 32, "maximum_seconds": 2400},
        "server_policy": "low confidence preserves raw fields but ignores category/skill for recommendations, assignment, occupancy and incident aggregation",
    })
    runtime.update(verified_at_utc=datetime.now(UTC).isoformat(), inference_calls_this_check=0)
    old_append(OUT / "runtime.json", runtime)
    old_append(OUT / "images.json", h.read(BASE / "crossfield-diagnostic-01/images.json"))
    h.prepare_images()

    reused = []
    for number in (1, 2):
        source = PREVIOUS / f"request-{number:02d}"
        response = h.read(source / "response.json")
        expected = plans()[number - 1]
        if response["case"]["id"] != expected["case"]["id"] or response["kind"] != expected["kind"]:
            raise RuntimeError("REUSE_CASE_MISMATCH")
        if response["model_digest"] != h.DIGEST or response["prompt_version"] != PROMPT_VERSION:
            raise RuntimeError("REUSE_MODEL_OR_PROMPT_MISMATCH")
        folder = OUT / f"request-{number:02d}"
        folder.mkdir()
        shutil.copy2(source / "response.json", folder / "response.json")
        old_append(folder / "reservation.json", {
            "number": number,
            "case": response["case"]["id"],
            "kind": response["kind"],
            "new_inference": False,
            "reused_from": str(source.relative_to(ROOT)),
            "source_response_sha256": h.sha(source / "response.json"),
            "reason": "identical model, digest, prompt version, case input and parameters; server-policy-only reevaluation",
        })
        old_append(folder / "judgement.json", {
            "continue": True,
            "response_sha256": h.sha(folder / "response.json"),
            "semantic_status": "PENDING_FINAL_FIXED_SET_ASSESSMENT",
            "new_inference": False,
        })
        reused.append({"number": number, "case": response["case"]["id"],
                       "response_sha256": h.sha(folder / "response.json")})
    old_append(OUT / "reuse.json", {
        "count": 2,
        "new_model_calls": 0,
        "not_cross_candidate_best_of": True,
        "basis": "candidate-02 is the same final r3fix2b prompt/model candidate; only the newly approved server decision policy changed",
        "items": reused,
    })
    print("R3POLICY FROZEN; 2 exact responses reused; zero new inference")


def collect_one():
    before = len(_reservations())
    h.execute_next()
    after = len(_reservations())
    if after == before:
        raise RuntimeError("NO_RESERVATION_RESOURCE_OR_GUARD_STOP")
    folder = OUT / f"request-{after:02d}"
    if (OUT / "stop.json").exists():
        raise RuntimeError("STRUCTURAL_PROVIDER_OR_RESOURCE_STOP")
    response = h.read(folder / "response.json")
    old_append(folder / "judgement.json", {
        "continue": True,
        "response_sha256": h.sha(folder / "response.json"),
        "semantic_status": "PENDING_FINAL_FIXED_SET_ASSESSMENT",
        "new_inference": True,
        "note": "Collection continuation only; not an individual semantic PASS.",
    })


def collect_all():
    while len(_reservations()) < 32:
        collect_one()
    actual = [h.read(path) for path in _reservations() if h.read(path).get("new_inference")]
    old_append(OUT / "collection-complete.json", {
        "status": "FIXED_SET_COLLECTED_PENDING_SEMANTIC_ASSESSMENT",
        "fixed_requests": 32,
        "reused_requests": 2,
        "new_requests": len(actual),
        "first_new_reserved_unix": actual[0]["reserved_unix"],
        "last_new_reserved_unix": actual[-1]["reserved_unix"],
        "elapsed_seconds": actual[-1]["reserved_unix"] - actual[0]["reserved_unix"],
    })
    print("FIXED SET COLLECTED")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["freeze", "next", "collect-all", "child"])
    parser.add_argument("--number", type=int)
    args = parser.parse_args()
    if args.mode == "freeze":
        freeze()
    elif args.mode == "child":
        h.child(args.number)
    elif args.mode == "next":
        collect_one()
    else:
        collect_all()
