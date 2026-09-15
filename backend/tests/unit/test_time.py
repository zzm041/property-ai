"""UTC persistence and Property business date tests."""

from datetime import UTC, datetime, timedelta, timezone

import pytest
from sqlalchemy.dialects import mysql

from app.core.time import (
    UTCDateTime,
    current_business_date,
    to_business_date,
    utc_now,
    validate_timezone,
)


def test_utc_now_is_aware_utc() -> None:
    now = utc_now()

    assert now.tzinfo is UTC
    assert now.utcoffset() == timedelta(0)


def test_utc_datetime_normalizes_non_utc_input() -> None:
    column_type = UTCDateTime()
    source = datetime(2026, 9, 15, 8, 30, tzinfo=timezone(timedelta(hours=8)))

    stored = column_type.process_bind_param(source, mysql.dialect())
    restored = column_type.process_result_value(stored, mysql.dialect())

    assert stored == datetime(2026, 9, 15, 0, 30)
    assert restored == datetime(2026, 9, 15, 0, 30, tzinfo=UTC)


def test_utc_datetime_rejects_naive_input() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        UTCDateTime().process_bind_param(
            datetime(2026, 9, 15, 0, 30),
            mysql.dialect(),
        )


def test_business_date_crosses_utc_day_for_shanghai() -> None:
    instant = datetime(2026, 9, 15, 16, 30, tzinfo=UTC)

    assert str(to_business_date(instant, "Asia/Shanghai")) == "2026-09-16"


def test_business_date_accepts_aware_non_utc_input() -> None:
    instant = datetime(
        2026,
        12,
        31,
        23,
        30,
        tzinfo=timezone(timedelta(hours=8)),
    )

    assert str(to_business_date(instant, "Asia/Shanghai")) == "2026-12-31"


def test_business_date_handles_dst_calendar_boundary() -> None:
    before_midnight = datetime(2026, 11, 1, 3, 30, tzinfo=UTC)
    after_midnight = datetime(2026, 11, 1, 4, 30, tzinfo=UTC)

    assert str(to_business_date(before_midnight, "America/New_York")) == "2026-10-31"
    assert str(to_business_date(after_midnight, "America/New_York")) == "2026-11-01"


def test_current_business_date_uses_injected_clock() -> None:
    result = current_business_date(
        "Asia/Shanghai",
        clock=lambda: datetime(2026, 1, 31, 16, 1, tzinfo=UTC),
    )

    assert str(result) == "2026-02-01"


def test_business_date_rejects_naive_datetime() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        to_business_date(datetime(2026, 9, 15, 1, 0), "Asia/Shanghai")


def test_unknown_iana_timezone_is_rejected() -> None:
    with pytest.raises(ValueError, match="Unknown IANA timezone"):
        validate_timezone("Mars/Olympus_Mons")
