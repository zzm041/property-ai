"""D9: repeated dispatch-wait observations collapse instead of piling up."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, func, insert, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.model.work_order import WorkOrderLog
from app.service.analysis import AnalysisService
from app.service.auto_dispatch import AutoDispatchService
from app.service.log_retention import (
    DEFAULT_EVENT_CODES,
    WORK_ORDER_LOGS,
    apply_prune,
    plan_prune,
)

NOW = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)


@dataclass
class FakeLogRepository:
    """Stands in for WorkOrderRepository's log half without a database."""

    rows: list[WorkOrderLog]

    def has_dispatch_wait_attempt(self, *, work_order_id: int, request_id: str) -> bool:
        return any(
            row.work_order_id == work_order_id and row.request_id == request_id
            for row in self.rows
        )

    def latest_dispatch_wait(self, *, work_order_id: int, tenant_id: int, property_id: int):
        matches = [
            row
            for row in self.rows
            if row.work_order_id == work_order_id
            and row.tenant_id == tenant_id
            and row.property_id == property_id
        ]
        return matches[-1] if matches else None

    def add_log(self, log: WorkOrderLog) -> None:
        # A flush would apply the column default; do the same here so the
        # window comparison sees the timestamp the row would have in MySQL.
        if log.created_at is None:
            log.created_at = NOW
        self.rows.append(log)


def _service(window_seconds: int) -> tuple[AutoDispatchService, FakeLogRepository]:
    dispatcher = AutoDispatchService(None, settings=Settings(_env_file=None))  # type: ignore[arg-type]
    repository = FakeLogRepository(rows=[])
    dispatcher._work_orders = repository  # type: ignore[assignment]
    dispatcher._settings = Settings(
        _env_file=None, dispatch_wait_log_window_seconds=window_seconds
    )
    return dispatcher, repository


def _record() -> SimpleNamespace:
    return SimpleNamespace(
        id=17,
        tenant_id=5,
        property_id=5,
        status="PENDING_ASSIGNMENT",
        process_reason="NONE",
    )


BUSY_SNAPSHOT = ({"filter_reason": "BUSY"},)
IDLE_SNAPSHOT: tuple = ()


def test_repeated_wait_inside_window_writes_one_row(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.service.auto_dispatch.utc_now", lambda: NOW)
    dispatcher, repository = _service(3600)
    record = _record()

    dispatcher._record_wait(record, BUSY_SNAPSHOT, "dispatch-wait:17:1", aggregate=True)
    dispatcher._record_wait(record, BUSY_SNAPSHOT, "dispatch-wait:17:2", aggregate=True)
    dispatcher._record_wait(record, BUSY_SNAPSHOT, "dispatch-wait:17:3", aggregate=True)

    assert len(repository.rows) == 1
    assert repository.rows[0].payload_json["reason_code"] == "WAITING_IDLE"


def test_a_different_reason_is_a_state_change_and_is_written(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.service.auto_dispatch.utc_now", lambda: NOW)
    dispatcher, repository = _service(3600)
    record = _record()

    dispatcher._record_wait(record, BUSY_SNAPSHOT, "dispatch-wait:17:1", aggregate=True)
    dispatcher._record_wait(record, IDLE_SNAPSHOT, "dispatch-wait:17:2", aggregate=True)

    assert [row.payload_json["reason_code"] for row in repository.rows] == [
        "WAITING_IDLE",
        "WAITING_ELIGIBLE",
    ]


def test_the_same_reason_outside_the_window_is_written_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispatcher, repository = _service(3600)
    record = _record()

    monkeypatch.setattr("app.service.auto_dispatch.utc_now", lambda: NOW)
    dispatcher._record_wait(record, BUSY_SNAPSHOT, "dispatch-wait:17:1", aggregate=True)
    later = NOW + timedelta(hours=2)
    monkeypatch.setattr("app.service.auto_dispatch.utc_now", lambda: later)
    dispatcher._record_wait(record, BUSY_SNAPSHOT, "dispatch-wait:17:2", aggregate=True)

    assert len(repository.rows) == 2


def test_window_zero_keeps_the_historical_one_row_per_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.service.auto_dispatch.utc_now", lambda: NOW)
    dispatcher, repository = _service(0)
    record = _record()

    dispatcher._record_wait(record, BUSY_SNAPSHOT, "dispatch-wait:17:1", aggregate=True)
    dispatcher._record_wait(record, BUSY_SNAPSHOT, "dispatch-wait:17:2", aggregate=True)

    assert len(repository.rows) == 2


def test_task_attempts_keep_their_own_row_for_intent_correlation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The analysis task correlates its intent on the request id of its row."""
    monkeypatch.setattr("app.service.auto_dispatch.utc_now", lambda: NOW)
    dispatcher, repository = _service(3600)
    record = _record()

    dispatcher._record_wait(record, BUSY_SNAPSHOT, "analyze:17:aa")
    dispatcher._record_wait(record, BUSY_SNAPSHOT, "analyze:17:bb")

    assert [row.request_id for row in repository.rows] == ["analyze:17:aa", "analyze:17:bb"]


def test_the_same_request_id_never_writes_twice(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.service.auto_dispatch.utc_now", lambda: NOW)
    dispatcher, repository = _service(3600)
    record = _record()

    dispatcher._record_wait(record, BUSY_SNAPSHOT, "dispatch-wait:17:1", aggregate=True)
    dispatcher._record_wait(record, BUSY_SNAPSHOT, "dispatch-wait:17:1", aggregate=True)

    assert len(repository.rows) == 1


def test_the_sweep_request_id_is_stable_inside_one_window() -> None:
    service = AnalysisService(None)  # type: ignore[arg-type]
    service._settings = Settings(_env_file=None, dispatch_wait_log_window_seconds=3600)
    early = datetime(2026, 11, 11, 10, 0, tzinfo=UTC)
    same_window = datetime(2026, 11, 11, 10, 59, 59, tzinfo=UTC)
    next_window = datetime(2026, 11, 11, 11, 0, 1, tzinfo=UTC)

    service._clock = lambda: early
    first = service._sweep_request_id(17)
    service._clock = lambda: same_window
    assert service._sweep_request_id(17) == first
    service._clock = lambda: next_window
    assert service._sweep_request_id(17) != first


# --------------------------------------------------------------------------- #
# retention script
# --------------------------------------------------------------------------- #


@pytest.fixture()
def log_session() -> Session:
    engine = create_engine("sqlite://")
    WORK_ORDER_LOGS.create(engine)
    with Session(bind=engine) as session:
        yield session
    engine.dispose()


def _seed(session: Session, rows: list[dict[str, object]]) -> None:
    session.execute(insert(WORK_ORDER_LOGS), rows)
    session.commit()


def _count(session: Session) -> int:
    return int(session.scalar(select(func.count()).select_from(WORK_ORDER_LOGS)) or 0)


def test_dry_run_plans_without_deleting_a_row(log_session: Session) -> None:
    _seed(
        log_session,
        [
            {"id": 1, "work_order_id": 1, "event_code": "AUTO_DISPATCH_WAIT", "request_id": "a",
             "created_at": datetime(2026, 9, 1, 0, 0)},
            {"id": 2, "work_order_id": 1, "event_code": "AUTO_DISPATCH_WAIT", "request_id": "b",
             "created_at": datetime(2026, 11, 10, 0, 0)},
            {"id": 3, "work_order_id": 1, "event_code": "ASSIGN", "request_id": "c",
             "created_at": datetime(2026, 9, 1, 0, 0)},
        ],
    )

    plan = plan_prune(log_session, days=30, now=NOW)

    assert _count(log_session) == 3
    assert plan.expired_rows == 1
    assert plan.by_event_code == {"AUTO_DISPATCH_WAIT": 1}
    assert plan.total_rows == 1
    assert plan.as_dict()["event_codes"] == list(DEFAULT_EVENT_CODES)


def test_apply_only_removes_the_expired_repeated_observation(log_session: Session) -> None:
    _seed(
        log_session,
        [
            {"id": 1, "work_order_id": 1, "event_code": "AUTO_DISPATCH_WAIT", "request_id": "a",
             "created_at": datetime(2026, 9, 1, 0, 0)},
            {"id": 2, "work_order_id": 1, "event_code": "AUTO_DISPATCH_WAIT", "request_id": "b",
             "created_at": datetime(2026, 11, 10, 0, 0)},
            {"id": 3, "work_order_id": 1, "event_code": "ASSIGN", "request_id": "c",
             "created_at": datetime(2026, 9, 1, 0, 0)},
        ],
    )

    plan = plan_prune(log_session, days=30, now=NOW)
    deleted = apply_prune(log_session, plan)

    assert deleted == 1
    remaining = session_ids(log_session)
    assert remaining == [2, 3]


def test_collapse_daily_keeps_the_newest_row_of_each_day(log_session: Session) -> None:
    day = datetime(2026, 11, 10, 8, 0)
    _seed(
        log_session,
        [
            {"id": 10, "work_order_id": 17, "event_code": "AUTO_DISPATCH_WAIT", "request_id": "a",
             "created_at": day},
            {"id": 11, "work_order_id": 17, "event_code": "AUTO_DISPATCH_WAIT", "request_id": "b",
             "created_at": day + timedelta(minutes=5)},
            {"id": 12, "work_order_id": 17, "event_code": "AUTO_DISPATCH_WAIT", "request_id": "c",
             "created_at": day + timedelta(minutes=9)},
            {"id": 13, "work_order_id": 17, "event_code": "AUTO_DISPATCH_WAIT", "request_id": "d",
             "created_at": day + timedelta(days=1)},
        ],
    )

    plan = plan_prune(log_session, days=30, collapse_daily=True, now=NOW)
    assert plan.collapsed_rows == 2
    assert plan.expired_rows == 0
    assert _count(log_session) == 4

    deleted = apply_prune(log_session, plan)
    assert deleted == 2
    assert session_ids(log_session) == [12, 13]


def test_days_must_be_positive(log_session: Session) -> None:
    with pytest.raises(ValueError):
        plan_prune(log_session, days=0)


def session_ids(session: Session) -> list[int]:
    return [int(value) for value in session.scalars(select(WORK_ORDER_LOGS.c.id).order_by(WORK_ORDER_LOGS.c.id))]
