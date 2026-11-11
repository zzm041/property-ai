"""Retention for the append-only ``work_order_logs`` table (D9).

The table is the audit trail, so nothing here runs on its own: a caller (the
``backend/scripts/ops/prune_work_order_logs.py`` command) plans a prune first
and only then asks for it to be applied.

Two independent reductions are offered:

* **expiry** — drop rows of the given event codes that are older than ``days``;
* **collapse** — inside the retained window keep only the newest row per
  ``(work order, event code, day)``.

Only event codes whose rows are repeated observations are prunable, and the
list is a closed allow-list (``PRUNABLE_EVENT_CODES``): anything that records a
business decision (``CREATE``, ``ASSIGN``, ``REASSIGN``, ``ANALYZE`` …) is
refused outright, not merely skipped by default.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    MetaData,
    String,
    Table,

    delete,
    func,
    select,
)
from sqlalchemy.orm import Session

#: The **only** event codes a prune may delete. This is an allow-list, not a
#: default: business events (``CREATE``, ``ASSIGN``, ``REASSIGN``, ``ANALYZE``,
#: ``SLA_OVERDUE`` …) are the audit trail and stay untouched no matter what a
#: caller passes on the command line. Adding noise here is a deliberate code
#: change, never an operator flag.
PRUNABLE_EVENT_CODES: tuple[str, ...] = ("AUTO_DISPATCH_WAIT",)

#: Kept for callers that only need "what happens by default".
DEFAULT_EVENT_CODES: tuple[str, ...] = PRUNABLE_EVENT_CODES

#: Columns the prune needs. Declared separately from the ORM model so the same
#: statements run against MySQL and against the SQLite fixture the unit tests
#: build, and so a prune can never be widened into a row rewrite by accident.
WORK_ORDER_LOGS = Table(
    "work_order_logs",
    MetaData(),
    Column("id", BigInteger, primary_key=True),
    Column("work_order_id", BigInteger, nullable=False),
    Column("event_code", String(64), nullable=False),
    Column("request_id", String(128), nullable=False),
    Column("created_at", DateTime, nullable=False),
)


@dataclass(frozen=True)
class PrunePlan:
    """What a prune would do, computed without changing any row."""

    days: int
    cutoff: datetime
    event_codes: tuple[str, ...]
    collapse_daily: bool
    expired_rows: int
    collapsed_rows: int
    by_event_code: dict[str, int] = field(default_factory=dict)

    @property
    def total_rows(self) -> int:
        return self.expired_rows + self.collapsed_rows

    def as_dict(self) -> dict[str, object]:
        return {
            "days": self.days,
            "cutoff_utc": self.cutoff.isoformat(),
            "event_codes": list(self.event_codes),
            "collapse_daily": self.collapse_daily,
            "expired_rows": self.expired_rows,
            "collapsed_rows": self.collapsed_rows,
            "total_rows": self.total_rows,
            "by_event_code": dict(self.by_event_code),
        }


def _normalise_event_codes(event_codes: tuple[str, ...] | list[str] | None) -> tuple[str, ...]:
    codes = tuple(dict.fromkeys(code.strip() for code in (event_codes or DEFAULT_EVENT_CODES) if code.strip()))
    if not codes:
        raise ValueError("at least one event code is required")
    refused = [code for code in codes if code not in PRUNABLE_EVENT_CODES]
    if refused:
        # D9/P2: `--event-code CREATE` used to be accepted and would have
        # deleted 22 real business rows. Refuse the whole call instead.
        raise ValueError(
            "event code(s) " + ", ".join(refused) + " are not prunable; "
            "allowed: " + ", ".join(PRUNABLE_EVENT_CODES)
        )
    return codes


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def _expired_rows(
    session: Session,
    *,
    cutoff: datetime,
    event_codes: tuple[str, ...],
) -> tuple[int, dict[str, int]]:
    statement = (
        select(WORK_ORDER_LOGS.c.event_code, func.count())
        .where(
            WORK_ORDER_LOGS.c.event_code.in_(event_codes),
            WORK_ORDER_LOGS.c.created_at < cutoff,
        )
        .group_by(WORK_ORDER_LOGS.c.event_code)
    )
    by_event = {str(code): int(count) for code, count in session.execute(statement).all()}
    return sum(by_event.values()), by_event


def _collapse_statement(*, cutoff: datetime, event_codes: tuple[str, ...]):
    """Ids that lose against a newer row of the same work order/event/day."""
    ranked = (
        select(
            WORK_ORDER_LOGS.c.id.label("id"),
            func.row_number()
            .over(
                partition_by=(
                    WORK_ORDER_LOGS.c.work_order_id,
                    WORK_ORDER_LOGS.c.event_code,
                    func.date(WORK_ORDER_LOGS.c.created_at),
                ),
                order_by=WORK_ORDER_LOGS.c.id.desc(),
            )
            .label("rank_in_day"),
        )
        .where(
            WORK_ORDER_LOGS.c.event_code.in_(event_codes),
            WORK_ORDER_LOGS.c.created_at >= cutoff,
        )
        .subquery()
    )
    return select(ranked.c.id).where(ranked.c.rank_in_day > 1)


def _collapsed_count(
    session: Session,
    *,
    cutoff: datetime,
    event_codes: tuple[str, ...],
) -> int:
    ranked = _collapse_statement(cutoff=cutoff, event_codes=event_codes).subquery()
    statement = select(func.count()).select_from(ranked)
    return int(session.scalar(statement) or 0)


def plan_prune(
    session: Session,
    *,
    days: int = 30,
    event_codes: tuple[str, ...] | list[str] | None = None,
    collapse_daily: bool = False,
    now: datetime | None = None,
) -> PrunePlan:
    """Compute the prune without touching a single row."""
    if days < 1:
        raise ValueError("--days must be at least 1")
    codes = _normalise_event_codes(event_codes)
    cutoff = _aware(now or datetime.now(UTC)) - timedelta(days=days)
    expired, by_event = _expired_rows(session, cutoff=cutoff, event_codes=codes)
    collapsed = (
        _collapsed_count(session, cutoff=cutoff, event_codes=codes)
        if collapse_daily
        else 0
    )
    return PrunePlan(
        days=days,
        cutoff=cutoff,
        event_codes=codes,
        collapse_daily=collapse_daily,
        expired_rows=expired,
        collapsed_rows=collapsed,
        by_event_code=by_event,
    )


def apply_prune(
    session: Session,
    plan: PrunePlan,
    *,
    batch_size: int = 2000,
) -> int:
    """Delete what ``plan`` describes, in bounded batches. Returns the count."""
    if batch_size < 1:
        raise ValueError("batch_size must be at least 1")
    codes = plan.event_codes
    deleted = 0
    while True:
        ids = session.scalars(
            select(WORK_ORDER_LOGS.c.id)
            .where(
                WORK_ORDER_LOGS.c.event_code.in_(codes),
                WORK_ORDER_LOGS.c.created_at < plan.cutoff,
            )
            .order_by(WORK_ORDER_LOGS.c.id)
            .limit(batch_size)
        ).all()
        if not ids:
            break
        deleted += int(
            session.execute(
                delete(WORK_ORDER_LOGS).where(WORK_ORDER_LOGS.c.id.in_(list(ids)))
            ).rowcount
            or 0
        )
        session.commit()
        if len(ids) < batch_size:
            break
    if plan.collapsed_rows:
        while True:
            ids = session.scalars(
                _collapse_statement(cutoff=plan.cutoff, event_codes=codes)
                .order_by(None)
                .limit(batch_size)
            ).all()
            if not ids:
                break
            deleted += int(
                session.execute(
                    delete(WORK_ORDER_LOGS).where(WORK_ORDER_LOGS.c.id.in_(list(ids)))
                ).rowcount
                or 0
            )
            session.commit()
            if len(ids) < batch_size:
                break
    return deleted
