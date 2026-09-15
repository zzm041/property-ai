"""UTC persistence and Property-local business date utilities."""

from collections.abc import Callable
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import DateTime
from sqlalchemy.dialects import mysql
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator


def utc_now() -> datetime:
    """Return the current time as a timezone-aware UTC datetime."""

    return datetime.now(UTC)


def validate_timezone(timezone_name: str) -> ZoneInfo:
    """Return a validated IANA timezone."""

    try:
        return ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"Unknown IANA timezone: {timezone_name}") from exc


def to_business_date(instant: datetime, timezone_name: str) -> date:
    """Convert one absolute instant to the Property's local natural date."""

    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ValueError("Business date requires a timezone-aware datetime")
    timezone = validate_timezone(timezone_name)
    return instant.astimezone(UTC).astimezone(timezone).date()


def current_business_date(
    timezone_name: str,
    *,
    clock: Callable[[], datetime] = utc_now,
) -> date:
    """Return today's date for a Property using an injectable UTC clock."""

    return to_business_date(clock(), timezone_name)


class UTCDateTime(TypeDecorator[datetime]):
    """Persist aware datetimes as UTC DATETIME(6), restore them as aware UTC."""

    impl = DateTime
    cache_ok = True

    def load_dialect_impl(self, dialect: Dialect) -> DateTime:
        if dialect.name == "mysql":
            return dialect.type_descriptor(mysql.DATETIME(fsp=6))
        return dialect.type_descriptor(DateTime(timezone=False))

    def process_bind_param(
        self,
        value: datetime | None,
        dialect: Dialect,
    ) -> datetime | None:
        del dialect
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("UTCDateTime requires a timezone-aware datetime")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(
        self,
        value: datetime | None,
        dialect: Dialect,
    ) -> datetime | None:
        del dialect
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
