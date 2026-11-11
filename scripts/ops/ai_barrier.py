"""PA-132: operator tooling for the shared completion barrier.

The adapter writes `tmp/delivery-ai-shared/completion-unknown.json` when a qwen call
could neither succeed nor prove that its own client and the runtime process tree are gone
(`record_shared_completion_unknown`). While that file exists, `gate()` answers
`BLOCKED_SHARED_REQUEST_COMPLETION_UNKNOWN_NO_REPLAY` for *every* request in both scopes,
so a single UNKNOWN takes the whole AI chain down.

Until now the only cure was to delete that file by hand: unrecorded, unreviewable, and it
also destroyed the evidence of what had happened. This module makes the release path
explicit:

- `describe` / `list` read the barrier and say what it is (which call, when, which scopes);
- `resolve` archives the barrier under `resolved/` with an appended operator decision
  (who, when, why, the original file sha256) and writes a receipt next to it.

What this deliberately does NOT do: it does not fabricate a `config.resolutions` entry.
Those entries are the reviewed path for "this UNKNOWN may be replayed", they are pinned by
sha256 to a resolution report plus a `PASS_SCOPED_CALL_RESOLUTION` review, and the running
adapter only reads them at startup. Removing the barrier restores service *without* a
restart because `gate()` checks for the file on every request, and that is exactly the
power an operator already had - now with a trace instead of a silent `rm`.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

BARRIER_FORMAT = "property-ai-shared-model-completion-barrier-v1"
DEFAULT_BARRIER = Path("tmp") / "delivery-ai-shared" / "completion-unknown.json"
RESOLVED_DIRNAME = "resolved"
AFFECTED_SCOPES = ("work_order_only", "rag")


def _utc_stamp() -> str:
    import datetime as _dt

    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def remediation_for(call_id: str, barrier_path: Path) -> dict[str, Any]:
    """The exact command an operator should run, carried inside the barrier itself."""
    return {
        "command": f'deploy/ai-runtime.ps1 resolve-unknown -Call {call_id} -Reason "<why it is safe to release>"',
        "effect": "archives the barrier under resolved/ and writes a receipt; no restart, no replay authorization",
        "requires_restart": False,
        "affected_scopes": list(AFFECTED_SCOPES),
        "barrier": str(barrier_path),
    }


@dataclass(frozen=True)
class Barrier:
    path: Path
    payload: dict[str, Any]
    sha256: str

    @property
    def call_id(self) -> str:
        return str(self.payload.get("call_id") or self.payload.get("record") or "")

    @property
    def scopes(self) -> tuple[str, ...]:
        scopes = self.payload.get("affected_scopes")
        if isinstance(scopes, list) and scopes:
            return tuple(str(item) for item in scopes)
        scope = self.payload.get("scope")
        return (str(scope),) if scope else AFFECTED_SCOPES


class BarrierError(RuntimeError):
    pass


def read_barrier(path: Path | str) -> Barrier:
    barrier_path = Path(path)
    if not barrier_path.is_file():
        raise BarrierError(f"no completion barrier at {barrier_path}")
    try:
        payload = json.loads(barrier_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise BarrierError(f"completion barrier is unreadable: {exc}") from exc
    if not isinstance(payload, dict):
        raise BarrierError("completion barrier is not a JSON object")
    if payload.get("format") != BARRIER_FORMAT:
        raise BarrierError(f"unexpected barrier format {payload.get('format')!r}")
    return Barrier(path=barrier_path, payload=payload, sha256=_sha256(barrier_path))


def describe(barrier: Barrier) -> dict[str, Any]:
    return {
        "barrier": str(barrier.path),
        "sha256": barrier.sha256,
        "call_id": barrier.call_id,
        "utc": barrier.payload.get("utc"),
        "status": barrier.payload.get("status"),
        "reason": barrier.payload.get("reason"),
        "remote_completion": barrier.payload.get("remote_completion"),
        "owned_exit_confirmed": barrier.payload.get("owned_exit_confirmed"),
        "scopes": list(barrier.scopes),
        "gate": "BLOCKED_SHARED_REQUEST_COMPLETION_UNKNOWN_NO_REPLAY",
        "remediation": barrier.payload.get("remediation")
        or remediation_for(barrier.call_id, barrier.path),
    }


def resolve(
    path: Path | str,
    *,
    call: str,
    reason: str,
    operator: str | None = None,
    resolved_dir: Path | str | None = None,
) -> dict[str, Any]:
    """Archive the barrier with an operator decision and return the receipt.

    Refuses when the barrier names a different call than the one the operator asked about,
    when the reason is empty, or when there is nothing to resolve - a silent success would
    hide a typo and leave the chain blocked.
    """
    if not reason or not reason.strip():
        raise BarrierError("a resolution reason is required")
    barrier = read_barrier(path)
    if call and barrier.call_id and barrier.call_id != call:
        raise BarrierError(
            f"barrier is for {barrier.call_id!r}, not {call!r}; refusing to release a different request"
        )
    resolved_root = Path(resolved_dir) if resolved_dir else barrier.path.parent / RESOLVED_DIRNAME
    resolved_root.mkdir(parents=True, exist_ok=True)
    stamp = _utc_stamp()
    stem = f"completion-unknown-{stamp.replace(':', '').replace('-', '')}-{barrier.call_id or 'unknown'}"
    archive_path = resolved_root / f"{stem}.json"
    if archive_path.exists():
        raise BarrierError(f"resolution archive already exists: {archive_path}")
    receipt_path = resolved_root.parent / f"resolution-receipt-{stamp.replace(':', '').replace('-', '')}.json"
    receipt = {
        "format": "property-ai-completion-barrier-resolution-v1",
        "utc": stamp,
        "operator": operator or os.environ.get("USERNAME") or os.environ.get("USER") or "unknown",
        "call_id": barrier.call_id,
        "reason": reason.strip(),
        "action": "BARRIER_ARCHIVED_AND_RELEASED",
        "restart_required": False,
        "replay_authorized": False,
        "barrier": {
            "original_path": str(barrier.path),
            "archive_path": str(archive_path),
            "sha256": barrier.sha256,
            "record": barrier.payload,
        },
        "gate_after": "READY",
        "note": (
            "the adapter checks for the barrier file on every request, so archiving it restores "
            "service without a restart; a replayed input still has to pass the reviewed "
            "config.resolutions path and is not authorized by this receipt"
        ),
    }
    original_bytes = barrier.path.read_bytes()
    archived = dict(barrier.payload)
    archived["resolution"] = {
        "utc": stamp,
        "operator": receipt["operator"],
        "reason": receipt["reason"],
        "action": receipt["action"],
        "receipt": str(receipt_path),
        "original_sha256": barrier.sha256,
    }
    archive_path.write_text(json.dumps(archived, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    receipt["barrier"]["bytes_preserved"] = len(original_bytes)
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    barrier.path.unlink()
    return receipt


def list_barriers(path: Path | str) -> list[dict[str, Any]]:
    barrier_path = Path(path)
    if not barrier_path.is_file():
        return []
    return [describe(read_barrier(barrier_path))]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="release a shared AI completion barrier, with a trace")
    parser.add_argument("--barrier", default=str(DEFAULT_BARRIER), help="path to completion-unknown.json")
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("list")
    show = sub.add_parser("show")
    show.add_argument("--call")
    release = sub.add_parser("resolve")
    release.add_argument("--call", required=True)
    release.add_argument("--reason", required=True)
    release.add_argument("--operator")
    release.add_argument("--resolved-dir")
    args = parser.parse_args(argv)
    barrier_path = Path(args.barrier)
    try:
        if args.action == "list":
            print(json.dumps(list_barriers(barrier_path), ensure_ascii=False, indent=2))
            return 0
        if args.action == "show":
            info = describe(read_barrier(barrier_path))
            if args.call and info["call_id"] != args.call:
                raise BarrierError(f"barrier is for {info['call_id']!r}, not {args.call!r}")
            print(json.dumps(info, ensure_ascii=False, indent=2))
            return 0
        receipt = resolve(
            barrier_path,
            call=args.call,
            reason=args.reason,
            operator=args.operator,
            resolved_dir=args.resolved_dir,
        )
        print(json.dumps(receipt, ensure_ascii=False, indent=2))
        return 0
    except BarrierError as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":  # pragma: no cover - CLI entry
    raise SystemExit(main())
