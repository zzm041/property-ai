"""t216: one provider call event is one ai_call_logs row.

The duplicate-write finding from PA-201 grouped ``ai_call_logs`` by request,
provider, operation, status, latency and input hash -- which is also how a
*retry* looks, because a retried worker re-runs the same stage under the same
request id and the transport failure that produced attempt 1 usually produces
attempt 2 with the same 0 ms latency. This module pins both halves of the fix:

* a genuine repeat of the same event (same attempt) writes one row, and
* attempt 2 or 3 still writes its own row, and
* normal fan-out (EMBEDDING / RERANK / LLM under one request) still writes three.
"""

from __future__ import annotations

from datetime import datetime, UTC

import pytest
from sqlalchemy import MetaData, Table, create_engine, select
from sqlalchemy.orm import Session

from app.model.ai import AiCallLog
from app.repository.ai import AiRepository

NOW = datetime(2026, 11, 12, 11, 0, 0, tzinfo=UTC)
_NEXT_ID = 0


def _sqlite_table(engine) -> None:
    """Create the table without the MySQL-only bits.

    The model's check constraints say ``BINARY provider_type IN (...)`` and the
    timestamp default is ``CURRENT_TIMESTAMP(6)``, neither of which sqlite can
    parse. The ORM still inserts through ``AiCallLog.__table__`` because the
    physical table keeps the same name, columns and primary key.
    """

    columns = []
    for column in AiCallLog.__table__.columns:
        clone = column.copy()
        clone.server_default = None
        columns.append(clone)
    Table(AiCallLog.__tablename__, MetaData(), *columns).create(engine)


@pytest.fixture()
def session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    _sqlite_table(engine)
    with Session(engine, autoflush=True) as active:
        yield active
    engine.dispose()


def _row(
    *,
    request_id: str = "req-216",
    provider_type: str = "LLM",
    operation: str = "structured_completion",
    status: str = "SUCCESS",
    attempt: int = 1,
    latency_ms: int = 1200,
) -> AiCallLog:
    # sqlite does not auto-increment a BIGINT primary key, so the test assigns
    # ids itself; MySQL does not need that and never sees these values.
    global _NEXT_ID
    _NEXT_ID += 1
    return AiCallLog(
        id=_NEXT_ID,
        tenant_id=5,
        property_id=5,
        work_order_id=700,
        request_id=request_id,
        provider_type=provider_type,
        provider_mode="PRODUCTION",
        operation=operation,
        biz_type="WORK_ORDER_ANALYSIS",
        prompt_version="wo-analysis-v26-summary-fidelity",
        model_version="qwen3.5:4b",
        status=status,
        started_at=NOW,
        finished_at=NOW,
        latency_ms=latency_ms,
        attempt=attempt,
        input_hash="a" * 64,
        content_fingerprint="b" * 64,
        code_version="c" * 40,
    )


def _count(session: Session) -> int:
    return len(session.scalars(select(AiCallLog)).all())


def test_writing_the_same_event_twice_lands_one_row(session: Session) -> None:
    repository = AiRepository(session)

    first = repository.add_call_log(_row())
    second = repository.add_call_log(_row(latency_ms=9999))

    assert first.id == second.id
    assert _count(session) == 1


def test_the_same_event_in_one_unflushed_transaction_lands_one_row() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    _sqlite_table(engine)
    # Retrieval audits run with autoflush off, so the SELECT cannot see the row
    # this transaction already staged; the pending-object check has to catch it.
    with Session(engine, autoflush=False) as session:
        repository = AiRepository(session)
        repository.add_call_log(_row())
        repository.add_call_log(_row())
        session.flush()
        assert _count(session) == 1
    engine.dispose()


def test_fan_out_across_operations_still_writes_three_rows(session: Session) -> None:
    repository = AiRepository(session)

    for provider_type, operation in (
        ("EMBEDDING", "embed"),
        ("RERANK", "rerank"),
        ("LLM", "structured_completion"),
    ):
        repository.add_call_log(
            _row(provider_type=provider_type, operation=operation)
        )

    assert _count(session) == 3


def test_a_retry_attempt_writes_its_own_row(session: Session) -> None:
    repository = AiRepository(session)

    repository.add_call_log(_row(status="FAILED", latency_ms=0, attempt=1))
    repository.add_call_log(_row(status="FAILED", latency_ms=0, attempt=2))
    repository.add_call_log(_row(status="FAILED", latency_ms=0, attempt=3))

    assert _count(session) == 3


def test_a_different_status_is_a_different_event(session: Session) -> None:
    repository = AiRepository(session)

    repository.add_call_log(_row(status="SUCCESS"))
    repository.add_call_log(_row(status="INVALID"))

    assert _count(session) == 2


def test_a_different_provider_is_a_different_event(session: Session) -> None:
    repository = AiRepository(session)

    repository.add_call_log(_row(provider_type="VLM", operation="inspect_image"))
    repository.add_call_log(_row(provider_type="ASR", operation="inspect_image"))

    assert _count(session) == 2


def test_an_empty_request_id_is_never_collapsed(session: Session) -> None:
    """Rows without a request identity cannot be matched to an event."""

    repository = AiRepository(session)

    repository.add_call_log(_row(request_id=""))
    repository.add_call_log(_row(request_id=""))

    assert _count(session) == 2


def test_the_guard_reads_the_database_not_the_identity_map(session: Session) -> None:
    """A row written by an earlier transaction is still recognised."""

    repository = AiRepository(session)
    repository.add_call_log(_row())
    session.commit()

    repository.add_call_log(_row())

    assert _count(session) == 1
