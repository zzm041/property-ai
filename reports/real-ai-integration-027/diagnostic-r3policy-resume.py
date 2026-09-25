"""Collector-only resume after reused reservations lacked a wall-clock field.

No model request was reserved or sent before this fix.  The frozen prompt,
source, samples, model, parameters and request numbering remain unchanged.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
import time

HERE = Path(__file__).parent
spec = importlib.util.spec_from_file_location("policy_run", HERE / "diagnostic-r3policy.py")
run = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run)
h = run.h


def next_number(directory: Path, *, now: float | None = None) -> int:
    now = time.time() if now is None else now
    if (directory / "stop.json").exists():
        raise RuntimeError("POLICY_DIAGNOSTIC_STOPPED")
    rows = sorted(directory.glob("request-*/reservation.json"))
    if len(rows) >= 32:
        raise RuntimeError("FIXED_REQUEST_LIMIT")
    if run.remaining_seconds(0, now) <= 0:
        raise RuntimeError("WALL_WINDOW_EXPIRED")
    if rows:
        last = rows[-1].parent
        if not (last / "response.json").exists():
            raise RuntimeError("PRIOR_REQUEST_UNKNOWN_DO_NOT_REPLAY")
        if not (last / "judgement.json").exists():
            raise RuntimeError("COLLECTION_JUDGEMENT_REQUIRED")
        judgement = h.read(last / "judgement.json")
        if not judgement.get("continue") or judgement.get("response_sha256") != h.sha(last / "response.json"):
            raise RuntimeError("PRIOR_STAGE_NOT_ACCEPTED")
    return len(rows) + 1


h.next_number = next_number
if not (run.OUT / "collector-resume.json").exists():
    run.old_append(run.OUT / "collector-resume.json", {
        "reason": "The two explicitly reused reservations have no reserved_unix because they start no new window; inherited collector read that absent field before the approved override.",
        "state_before_fix": "2 reused responses, 0 new reservations, 0 new inference",
        "scope": "request numbering and new-window accounting only",
        "frozen_candidate_changed": False,
        "automatic_retry": False,
    })

run.collect_all()
