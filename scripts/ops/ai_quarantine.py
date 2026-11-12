"""Clear stale content-quarantine refusals without deleting a single audit row.

F-7 (t214): PA-169 quarantined report *content* for 24 hours once its
``content_fingerprint`` had produced an unusable answer. The probe never asked
which code had produced that answer, so a text that a since-fixed build had
rejected kept short-circuiting to ``AI.INPUT_QUARANTINED`` with a 0 ms provider
call. The product fix (``buildinfo.code_version``) makes new refusals
self-scoping, but rows written before it carry no version and still need an
operator decision - plus there is always the case where the operator simply
knows the content is fine.

This tool makes that decision explicit:

* it never deletes anything - the matching rows get ``quarantine_cleared_at``
  stamped, and the probe ignores stamped rows;
* it never runs by accident - one of ``--older-than``, ``--content-fingerprint``
  or ``--all`` is required;
* it always leaves a trace - every cleared row gets an ``audit_logs`` row with
  the fingerprint, the version that produced the refusal and the operator's
  cut-off, so "why did this text reach the model again" is answerable.

Usage:

    python scripts/ops/ai_quarantine.py --older-than 24h --dry-run
    python scripts/ops/ai_quarantine.py --content-fingerprint 11ccf07c6e42668e
    python scripts/ops/ai_quarantine.py --older-than 1h --tenant-id 5 --json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND = REPO_ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from sqlalchemy import create_engine, select, update  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.time import utc_now  # noqa: E402
from app.model.ai import AiCallLog  # noqa: E402
from app.model.identity import AuditLog  # noqa: E402
from app.service.analysis.context import QUARANTINE_ERROR_CODES  # noqa: E402

AUDIT_ACTION = "AI_QUARANTINE_CLEARED"
AUDIT_RESOURCE = "AI_CALL_LOG"
_DURATION = re.compile(r"^\s*(\d+)\s*([smhdw]?)\s*$", re.IGNORECASE)
_UNIT_SECONDS = {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}


def parse_duration(value: str) -> timedelta:
    """``90``, ``90s``, ``30m``, ``24h``, ``7d``, ``1w`` - the unit defaults to seconds."""

    match = _DURATION.match(value or "")
    if not match:
        raise ValueError(f"not a duration: {value!r} (use e.g. 30m, 24h, 7d)")
    amount, unit = match.groups()
    return timedelta(seconds=int(amount) * _UNIT_SECONDS[unit.lower()])


@dataclass
class ClearReport:
    cutoff: str
    dry_run: bool
    selected: int = 0
    cleared: int = 0
    fingerprints: list[str] = field(default_factory=list)
    rows: list[dict[str, object]] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "cutoff": self.cutoff,
            "dry_run": self.dry_run,
            "selected": self.selected,
            "cleared": self.cleared,
            "fingerprints": self.fingerprints,
            "rows": self.rows,
        }


class QuarantineClearer:
    """Stamp ``quarantine_cleared_at`` on refusal rows, and audit each stamp."""

    def __init__(self, session: Session, *, dry_run: bool) -> None:
        self._session = session
        self._dry_run = dry_run

    def clear(
        self,
        *,
        older_than: timedelta | None,
        content_fingerprint: str | None,
        tenant_id: int | None,
        limit: int,
        actor_user_id: int | None,
        reason: str,
    ) -> ClearReport:
        now = utc_now()
        cutoff = now - (older_than or timedelta(0))
        report = ClearReport(
            cutoff=cutoff.astimezone().isoformat(timespec="seconds"),
            dry_run=self._dry_run,
        )
        query = (
            select(AiCallLog)
            .where(
                AiCallLog.quarantine_cleared_at.is_(None),
                AiCallLog.content_fingerprint.is_not(None),
                AiCallLog.error_code.in_(tuple(QUARANTINE_ERROR_CODES)),
                AiCallLog.created_at <= cutoff,
            )
            .order_by(AiCallLog.id.asc())
            .limit(limit)
        )
        if content_fingerprint:
            query = query.where(AiCallLog.content_fingerprint == content_fingerprint)
        if tenant_id:
            query = query.where(AiCallLog.tenant_id == tenant_id)
        records = list(self._session.scalars(query).all())
        report.selected = len(records)
        if not records:
            return report

        ids = [record.id for record in records]
        if self._dry_run:
            for record in records:
                report.rows.append(self._describe(record, cleared=False))
            report.fingerprints = sorted(
                {str(record.content_fingerprint) for record in records}
            )
            return report

        self._session.execute(
            update(AiCallLog)
            .where(AiCallLog.id.in_(ids))
            .values(quarantine_cleared_at=now)
        )
        request_id = f"quarantine-clear-{uuid.uuid4().hex}"
        for record in records:
            self._session.add(
                AuditLog(
                    actor_user_id=actor_user_id,
                    tenant_id=record.tenant_id,
                    property_id=record.property_id,
                    action=AUDIT_ACTION,
                    resource_type=AUDIT_RESOURCE,
                    resource_id=record.id,
                    request_id=request_id,
                    details_json={
                        "cleared_at": now.isoformat(),
                        "content_fingerprint": record.content_fingerprint,
                        "work_order_id": record.work_order_id,
                        "error_code": record.error_code,
                        "refusal_code_version": record.code_version,
                        "prompt_version": record.prompt_version,
                        "refused_at": record.created_at.isoformat(),
                        "reason": reason,
                    },
                )
            )
            report.rows.append(self._describe(record, cleared=True))
        self._session.commit()
        report.cleared = len(records)
        report.fingerprints = sorted(
            {str(record.content_fingerprint) for record in records}
        )
        return report

    @staticmethod
    def _describe(record: AiCallLog, *, cleared: bool) -> dict[str, object]:
        return {
            "id": record.id,
            "tenant_id": record.tenant_id,
            "property_id": record.property_id,
            "work_order_id": record.work_order_id,
            "content_fingerprint": record.content_fingerprint,
            "error_code": record.error_code,
            "code_version": record.code_version,
            "prompt_version": record.prompt_version,
            "refused_at": record.created_at.isoformat(),
            "cleared": cleared,
        }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ai_quarantine",
        description="Stamp stale content-quarantine refusals so the text is analysed again.",
    )
    parser.add_argument(
        "--database-url",
        default=None,
        help="SQLAlchemy URL (default: the app's active database URL)",
    )
    parser.add_argument(
        "--older-than",
        default=None,
        help="only clear refusals older than this (30m, 24h, 7d); required unless --all",
    )
    parser.add_argument(
        "--content-fingerprint",
        default=None,
        help="only clear this fingerprint (see ai_call_logs.content_fingerprint)",
    )
    parser.add_argument("--tenant-id", type=int, default=None, help="only clear this tenant")
    parser.add_argument(
        "--actor-user-id", type=int, default=None, help="operator user id recorded in the audit row"
    )
    parser.add_argument(
        "--reason",
        default="operator cleared a stale content quarantine",
        help="why the content is considered analysable again (kept in the audit row)",
    )
    parser.add_argument("--limit", type=int, default=500, help="safety cap on rows per run")
    parser.add_argument("--dry-run", action="store_true", help="report what would change, write nothing")
    parser.add_argument("--all", action="store_true", help="clear every pending refusal (no cut-off)")
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    arguments = parser.parse_args(argv)
    if not arguments.all and not arguments.older_than and not arguments.content_fingerprint:
        parser.error("one of --older-than, --content-fingerprint or --all is required")
    try:
        older_than = parse_duration(arguments.older_than) if arguments.older_than else None
    except ValueError as error:
        parser.error(str(error))
        raise AssertionError from error  # pragma: no cover - argparse exits

    settings = get_settings()
    url = arguments.database_url or settings.active_database_url
    engine = create_engine(url, future=True)
    try:
        with Session(engine, future=True) as session:
            report = QuarantineClearer(session, dry_run=arguments.dry_run).clear(
                older_than=None if arguments.all else older_than,
                content_fingerprint=arguments.content_fingerprint,
                tenant_id=arguments.tenant_id,
                limit=arguments.limit,
                actor_user_id=arguments.actor_user_id,
                reason=arguments.reason,
            )
    finally:
        engine.dispose()

    if arguments.json:
        print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
        return 0
    verb = "would clear" if arguments.dry_run else "cleared"
    print(
        f"{verb} {report.selected if arguments.dry_run else report.cleared} "
        f"refusal row(s) older than {report.cutoff}"
    )
    for row in report.rows:
        print(
            "  "
            f"call={row['id']} tenant={row['tenant_id']} wo={row['work_order_id']} "
            f"code={row['error_code']} fingerprint={row['content_fingerprint']} "
            f"code_version={row['code_version']} refused_at={row['refused_at']}"
        )
    if report.rows and not arguments.dry_run:
        print("audit action: " + AUDIT_ACTION)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
