"""PA-179 (t191): keep reviewed call evidence out of the live budget ledger.

The AI runtime keeps its per-call reservations under ``calls/call-*/`` and the
budget gate counts *every* reservation it finds there. Reviewed evidence (the
``call-001`` … assets a launcher refuses to start without, or a call an operator
wants to keep for review) therefore used to spend the very budget it was meant
to prove, and an exhausted budget then surfaced as ``AI.UNAVAILABLE`` — the same
code a real provider outage produces.

The adapter already knows how to skip such records: it reads ``evidence_records``
from its config, keeps those reservations on disk, and reports them separately in
``/healthz`` as ``evidence_excluded``. What was missing is the *operator action*:
declare one call as evidence, record what was declared (sha256 + verdict) in an
append-only ledger, and make the declaration visible in the config the adapter
reads.

This tool does exactly that and nothing else:

* it never deletes, moves or rewrites a call directory — evidence that gets
  pruned is not evidence;
* it appends one JSON line per archive action to ``evidence-ledger.jsonl`` next
  to the runtime, with the sha256 of every file it vouched for;
* it updates only the ``evidence_records`` key of ``config.json`` and leaves the
  rest byte-identical in meaning;
* re-archiving an unchanged call is a no-op, and re-archiving a changed call
  appends a superseding line instead of editing history.

Archiving evidence *narrows* what the window counts. It is not a way to raise a
limit: ``limits`` and ``call_baseline`` stay untouched, which is why the runbook
section that documents this says so in as many words.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

DEFAULT_RUNTIME_DIR = Path("tmp") / "delivery-work-ai-runtime"
CALLS_DIRNAME = "calls"
CONFIG_NAME = "config.json"
LEDGER_NAME = "evidence-ledger.jsonl"
# The launcher rebuilds config.json on every start from this manifest, so the manifest is the
# durable declaration and config.json is only the copy the running adapter reads.
MANIFEST_NAME = "evidence-records.txt"
REQUIRED_FILES = ("reservation.json", "result.json", "input.json")
CALL_PREFIX = "call-"


class ArchiveRefused(RuntimeError):
    """The call cannot be declared evidence; nothing was written."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def record_sha256(files: Mapping[str, str]) -> str:
    """One digest over "name:sha256" lines, so a later change is detectable."""

    lines = [f"{name}:{files[name]}" for name in sorted(files)]
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def runtime_dir(path: str | os.PathLike[str] | None) -> Path:
    resolved = Path(path).expanduser() if path else DEFAULT_RUNTIME_DIR
    if not resolved.is_dir():
        raise ArchiveRefused(f"runtime directory is missing: {resolved}")
    return resolved


def load_config(runtime: Path) -> dict[str, Any]:
    config_path = runtime / CONFIG_NAME
    if not config_path.is_file():
        raise ArchiveRefused(f"the runtime config is missing: {config_path}")
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ArchiveRefused(f"the runtime config is not valid JSON ({config_path}): {exc}") from exc
    if not isinstance(config, dict):
        raise ArchiveRefused(f"the runtime config is not an object: {config_path}")
    return config


def evidence_records(config: Mapping[str, Any]) -> list[str]:
    declared = config.get("evidence_records", [])
    if declared is None:
        return []
    if not isinstance(declared, list) or any(not isinstance(item, str) or not item.strip() for item in declared):
        raise ArchiveRefused("config 'evidence_records' must be a list of non-empty call names")
    return sorted({item.strip() for item in declared})


def manifest_path(runtime: Path) -> Path:
    return runtime / MANIFEST_NAME


def read_manifest(runtime: Path) -> list[str]:
    path = manifest_path(runtime)
    if not path.is_file():
        return []
    names = []
    for line in path.read_text(encoding="utf-8").splitlines():
        name = line.strip()
        if not name:
            continue
        if not name.startswith(CALL_PREFIX) or ".." in name or "/" in name or "\\" in name:
            raise ArchiveRefused(f"evidence manifest line is not a call id ({path}): {name!r}")
        names.append(name)
    return sorted(set(names))


def declared_calls(runtime: Path, config: Mapping[str, Any]) -> list[str]:
    """Both places a declaration can live; the launcher-owned manifest wins on restart."""

    return sorted({*evidence_records(config), *read_manifest(runtime)})


def write_manifest(runtime: Path, names: Iterable[str]) -> Path:
    path = manifest_path(runtime)
    ordered = sorted({name.strip() for name in names if name and name.strip()})
    path.write_text("\n".join(ordered) + "\n", encoding="utf-8")
    return path


def resolve_call(runtime: Path, call: str) -> Path:
    name = (call or "").strip()
    if not name:
        raise ArchiveRefused("a call id is required, for example call-001")
    if not name.startswith(CALL_PREFIX) or "/" in name or "\\" in name or ".." in name:
        raise ArchiveRefused(f"'{name}' is not a call id (expected {CALL_PREFIX}NNN)")
    calls_root = (runtime / CALLS_DIRNAME).resolve()
    target = (calls_root / name).resolve()
    if calls_root not in target.parents:
        raise ArchiveRefused(f"'{name}' resolves outside the calls directory: {target}")
    if not target.is_dir():
        raise ArchiveRefused(f"no such call under {calls_root}: {name}")
    missing = [f for f in REQUIRED_FILES if not (target / f).is_file()]
    if missing:
        raise ArchiveRefused(
            f"{name} cannot be evidence: missing {', '.join(missing)}; an archived call must be replayable, "
            "so a partial record is refused instead of vouched for"
        )
    return target


def describe(call_dir: Path) -> dict[str, Any]:
    reservation = json.loads((call_dir / "reservation.json").read_text(encoding="utf-8"))
    result = json.loads((call_dir / "result.json").read_text(encoding="utf-8"))
    return {
        "call": call_dir.name,
        "kind": reservation.get("kind"),
        "model": reservation.get("model"),
        "input_hash": reservation.get("input_hash"),
        "reserved_at": reservation.get("utc"),
        "status": result.get("status"),
        "total_seconds": result.get("total_seconds"),
        "reason": result.get("reason"),
        "budget_counted": reservation.get("budget_counted"),
    }


def read_ledger(runtime: Path) -> list[dict[str, Any]]:
    path = runtime / LEDGER_NAME
    if not path.is_file():
        return []
    entries: list[dict[str, Any]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ArchiveRefused(f"evidence ledger line {number} is not JSON ({path}): {exc}") from exc
        if isinstance(entry, dict):
            entries.append(entry)
    return entries


def _append_ledger(runtime: Path, entry: Mapping[str, Any]) -> Path:
    path = runtime / LEDGER_NAME
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(dict(entry), ensure_ascii=False, sort_keys=True) + "\n")
    return path


def budget_snapshot(runtime: Path, declared: Iterable[str]) -> dict[str, Any]:
    """Same rule the adapter applies: declared calls are evidence, the rest are spend."""

    excluded = set(declared)
    counted: dict[str, int] = {}
    evidence: dict[str, int] = {}
    counted_records: list[str] = []
    evidence_records_found: list[str] = []
    for reservation in sorted((runtime / CALLS_DIRNAME).glob(f"{CALL_PREFIX}*/reservation.json")):
        name = reservation.parent.name
        try:
            kind = json.loads(reservation.read_text(encoding="utf-8")).get("kind")
        except json.JSONDecodeError:
            continue
        if name in excluded:
            evidence[kind] = evidence.get(kind, 0) + 1
            evidence_records_found.append(name)
        else:
            counted[kind] = counted.get(kind, 0) + 1
            counted_records.append(name)
    return {
        "counted": counted,
        "evidence_excluded": len(evidence_records_found),
        "evidence_records": evidence_records_found,
        "counted_records": counted_records,
    }


def archive(
    *,
    call: str,
    reason: str,
    runtime: str | os.PathLike[str] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    runtime = runtime_dir(runtime)
    config = load_config(runtime)
    declared = declared_calls(runtime, config)
    call_dir = resolve_call(runtime, call)
    details = describe(call_dir)
    files = {name: sha256_file(call_dir / name) for name in REQUIRED_FILES}
    record = record_sha256(files)
    ledger = read_ledger(runtime)
    previous = [entry for entry in ledger if entry.get("call") == call_dir.name]
    already = [entry for entry in previous if entry.get("record_sha256") == record]
    before = budget_snapshot(runtime, declared)

    if already:
        return {
            "status": "already-archived",
            "call": call_dir.name,
            "record_sha256": record,
            "files": files,
            "details": details,
            "budget_before": before,
            "budget_after": before,
            "config_changed": False,
            "ledger": str(runtime / LEDGER_NAME),
            "restart_required": False,
        }

    entry = {
        "archived_at": _utc_now(),
        "call": call_dir.name,
        "reason": reason or "reviewed evidence",
        "record_sha256": record,
        "files": files,
        "details": details,
        "supersedes": previous[-1].get("record_sha256") if previous else None,
        "ledger_entries_before": len(previous),
    }

    updated = sorted({*declared, call_dir.name})
    after = budget_snapshot(runtime, updated)
    config_changed = updated != declared
    if dry_run:
        return {
            "status": "dry-run",
            "call": call_dir.name,
            "record_sha256": record,
            "files": files,
            "details": details,
            "budget_before": before,
            "budget_after": after,
            "config_changed": config_changed,
            "ledger": str(runtime / LEDGER_NAME),
            "restart_required": config_changed,
        }

    manifest = None
    if config_changed:
        manifest = write_manifest(runtime, updated)
        config["evidence_records"] = updated
        (runtime / CONFIG_NAME).write_text(
            json.dumps(config, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    ledger_path = _append_ledger(runtime, entry)
    return {
        "status": "archived",
        "call": call_dir.name,
        "record_sha256": record,
        "files": files,
        "details": details,
        "budget_before": before,
        "budget_after": after,
        "config_changed": config_changed,
        "manifest": str(manifest) if manifest else str(manifest_path(runtime)),
        "ledger": str(ledger_path),
        "restart_required": config_changed,
    }


def on_disk_calls(runtime: Path) -> list[str]:
    """Call directories that actually exist: the only evidence the budget can count."""

    calls = runtime / CALLS_DIRNAME
    if not calls.is_dir():
        return []
    return sorted(
        child.name
        for child in calls.iterdir()
        if child.is_dir() and child.name.startswith(CALL_PREFIX)
    )


def reconcile(
    *,
    runtime: str | os.PathLike[str] | None = None,
    dry_run: bool = False,
    reason: str = "",
) -> dict[str, Any]:
    """Drop manifest entries whose call directory is gone (PA-179 / T194-1).

    A stale line is not harmless: every `work-ai-runtime.ps1 start` rebuilds
    `config.json` from this manifest, so a ghost declaration is promoted back
    into the budget the next time the adapter really restarts.
    """

    runtime = runtime_dir(runtime)
    config = load_config(runtime)
    declared = declared_calls(runtime, config)
    on_disk = on_disk_calls(runtime)
    on_disk_set = set(on_disk)
    ghosts = sorted(name for name in declared if name not in on_disk_set)
    kept = sorted(name for name in declared if name in on_disk_set)
    before = budget_snapshot(runtime, declared)
    result: dict[str, Any] = {
        "status": "clean" if not ghosts else ("dry-run" if dry_run else "reconciled"),
        "declared_before": len(declared),
        "declared_after": len(kept),
        "on_disk": len(on_disk),
        "ghosts": len(ghosts),
        "ghost_calls": ghosts,
        "kept_calls": kept,
        "budget_before": before,
        "budget_after": before,
        "config_changed": False,
        "manifest": str(manifest_path(runtime)),
        "ledger": str(runtime / LEDGER_NAME),
        "restart_required": False,
    }
    if not ghosts:
        return result

    after = budget_snapshot(runtime, kept)
    changed = kept != declared
    result.update(
        budget_after=after,
        config_changed=changed,
        restart_required=changed,
    )
    if dry_run:
        return result

    entry = {
        "event": "reconciled",
        "reconciled_at": _utc_now(),
        "reason": reason or "manifest entries whose call directory is gone",
        "declared_before": len(declared),
        "declared_after": len(kept),
        "removed": ghosts,
        "on_disk": on_disk,
    }
    if changed:
        write_manifest(runtime, kept)
        config["evidence_records"] = kept
        (runtime / CONFIG_NAME).write_text(
            json.dumps(config, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    result["ledger"] = str(_append_ledger(runtime, entry))
    return result


def ledger_summary(runtime: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    root = runtime_dir(runtime)
    config = load_config(root)
    declared = declared_calls(root, config)
    on_disk = on_disk_calls(root)
    on_disk_set = set(on_disk)
    return {
        "declared": len(declared),
        "on_disk": len(on_disk),
        "ghosts": len([name for name in declared if name not in on_disk_set]),
    }


def list_ledger(runtime: str | os.PathLike[str] | None = None) -> list[dict[str, Any]]:
    root = runtime_dir(runtime)
    config = load_config(root)
    declared = declared_calls(root, config)
    entries = []
    for entry in read_ledger(root):
        if entry.get("event") == "reconciled":
            entries.append(
                {
                    "event": "reconciled",
                    "call": None,
                    "archived_at": entry.get("reconciled_at"),
                    "reason": entry.get("reason"),
                    "record_sha256": None,
                    "input_hash": None,
                    "kind": None,
                    "declared": False,
                    "removed": entry.get("removed"),
                }
            )
            continue
        entries.append(
            {
                "call": entry.get("call"),
                "archived_at": entry.get("archived_at"),
                "reason": entry.get("reason"),
                "record_sha256": entry.get("record_sha256"),
                "input_hash": (entry.get("details") or {}).get("input_hash"),
                "kind": (entry.get("details") or {}).get("kind"),
                "declared": entry.get("call") in declared,
            }
        )
    return entries


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ai_evidence_archive.py",
        description="Declare one AI runtime call as reviewed evidence so it stops consuming the live budget.",
    )
    parser.add_argument("--runtime-dir", default=None, help=f"runtime directory (default: {DEFAULT_RUNTIME_DIR})")
    parser.add_argument("--call", default=None, help="call id to archive, for example call-001")
    parser.add_argument("--reason", default=None, help="why this call is evidence (kept in the ledger)")
    parser.add_argument("--dry-run", action="store_true", help="report what would change and write nothing")
    parser.add_argument("--list", action="store_true", help="print the evidence ledger")
    parser.add_argument(
        "--reconcile",
        action="store_true",
        help="drop manifest entries whose call directory is gone (they would otherwise return to the budget on restart)",
    )
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.list:
            entries = list_ledger(args.runtime_dir)
            summary = ledger_summary(args.runtime_dir)
            if args.json:
                print(json.dumps({"entries": entries, **summary}, ensure_ascii=False, indent=2))
            else:
                if not entries:
                    print("evidence ledger is empty")
                for entry in entries:
                    if entry["event"] == "reconciled":
                        removed = entry.get("removed") or []
                        print(
                            f"reconciled removed={len(removed)} at={entry['archived_at']} "
                            f"first={removed[0] if removed else None} reason={entry['reason']}"
                        )
                        continue
                    print(
                        f"{entry['call']} kind={entry['kind']} archived_at={entry['archived_at']} "
                        f"record={entry['record_sha256']} declared={entry['declared']} reason={entry['reason']}"
                    )
                print(
                    f"declared={summary['declared']} on_disk={summary['on_disk']} ghosts={summary['ghosts']}"
                )
            return 0
        if args.reconcile:
            result = reconcile(
                runtime=args.runtime_dir, dry_run=args.dry_run, reason=args.reason or ""
            )
            if args.json:
                print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
            else:
                print(
                    f"{result['status']}: declared {result['declared_before']} -> {result['declared_after']} "
                    f"on_disk={result['on_disk']} ghosts={result['ghosts']} "
                    f"restart_required={result['restart_required']}"
                )
                if result["ghost_calls"]:
                    print(f"removed: {' '.join(result['ghost_calls'])}")
                print(f"manifest: {result['manifest']}")
                print(f"ledger: {result['ledger']}")
            return 0
        if not args.call:
            print("a call id is required: --call call-001", file=sys.stderr)
            return 2
        result = archive(call=args.call, reason=args.reason or "", runtime=args.runtime_dir, dry_run=args.dry_run)
    except ArchiveRefused as exc:
        print(f"EVIDENCE_ARCHIVE_REFUSED: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        before, after = result["budget_before"], result["budget_after"]
        print(
            f"{result['status']}: {result['call']} record_sha256={result['record_sha256']} "
            f"declared={result['config_changed']} restart_required={result['restart_required']}"
        )
        print(f"counted before={before['counted']} evidence_before={before['evidence_excluded']}")
        print(f"counted after ={after['counted']} evidence_after ={after['evidence_excluded']}")
        print(f"ledger: {result['ledger']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
