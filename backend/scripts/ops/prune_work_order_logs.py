"""Prune repeated ``work_order_logs`` observations (D9).

``work_order_logs`` is an audit trail, so this command is **dry-run by
default**: it prints how many rows a prune would remove and exits without
writing. ``--apply`` is required to delete anything.

Why it exists: ``sweep_waiting_dispatch`` re-evaluates every work order that is
waiting for a dispatcher, and each sweep recorded a fresh
``AUTO_DISPATCH_WAIT`` row. In the delivery database that produced 105,467 of
106,438 ``work_order_logs`` rows (26 work orders, 40 days). The writer now
aggregates repeats inside ``DISPATCH_WAIT_LOG_WINDOW_SECONDS``; this command
handles the history that is already there.

Usage::

    # 只打印计划（默认，不写库）
    python backend/scripts/ops/prune_work_order_logs.py --days 30

    # 真正删除 30 天以前的 AUTO_DISPATCH_WAIT 行
    python backend/scripts/ops/prune_work_order_logs.py --days 30 --apply

    # 保留窗口内，每个工单每天只留最新一行
    python backend/scripts/ops/prune_work_order_logs.py --days 30 --collapse-daily --apply

Only repeated-observation event codes are eligible, and that list is closed
(``PRUNABLE_EVENT_CODES``): ``--event-code CREATE`` exits 2 before opening a
connection, so business history cannot be pruned by a flag.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import get_settings  # noqa: E402
from app.core.database import create_database_engine  # noqa: E402
from app.service.log_retention import (  # noqa: E402
    DEFAULT_EVENT_CODES,
    PRUNABLE_EVENT_CODES,
    apply_prune,
    plan_prune,
)
from sqlalchemy.orm import Session  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Prune repeated work_order_logs rows (D9).")
    parser.add_argument("--days", type=int, default=30, help="keep this many days (default 30)")
    parser.add_argument(
        "--event-code",
        action="append",
        dest="event_codes",
        default=None,
        help=(
            "repeatable; allow-list " + "/".join(PRUNABLE_EVENT_CODES) +
            "; any other code is refused before the database is touched"
        ),
    )
    parser.add_argument(
        "--collapse-daily",
        action="store_true",
        help="inside the kept window keep only the newest row per work order/event/day",
    )
    parser.add_argument("--apply", action="store_true", help="delete the rows (default is dry-run)")
    parser.add_argument("--batch-size", type=int, default=2000, help="rows per delete batch")
    parser.add_argument("--database-url", default=None, help="override DATABASE_URL")
    return parser


def refused_event_codes(event_codes: list[str] | None) -> list[str]:
    """Event codes the allow-list does not cover; empty means the call may run."""

    requested = event_codes or list(DEFAULT_EVENT_CODES)
    return [code for code in requested if code.strip() not in PRUNABLE_EVENT_CODES]


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.days < 1:
        print("--days must be at least 1", file=sys.stderr)
        return 2
    refused = refused_event_codes(args.event_codes)
    if refused:
        # Refuse before any connection is opened: a rejected call must not even
        # read the table, let alone delete from it.
        print(
            "refused: event code(s) " + ", ".join(refused) + " are not prunable; "
            "allowed: " + ", ".join(PRUNABLE_EVENT_CODES),
            file=sys.stderr,
        )
        return 2
    settings = get_settings() if args.database_url is None else None
    engine = create_database_engine(
        args.database_url or (settings.active_database_url if settings else "")
    )
    session = Session(bind=engine, autoflush=False, expire_on_commit=False)
    try:
        plan = plan_prune(
            session,
            days=args.days,
            event_codes=args.event_codes,
            collapse_daily=args.collapse_daily,
        )
        report: dict[str, object] = {
            "mode": "apply" if args.apply else "dry-run",
            "database": engine.url.database,
            **plan.as_dict(),
        }
        if args.apply and plan.total_rows:
            report["deleted_rows"] = apply_prune(session, plan, batch_size=args.batch_size)
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    finally:
        session.close()
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
