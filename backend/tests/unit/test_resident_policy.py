"""Resident ownership policy unit tests."""

from datetime import UTC, datetime, timedelta

from app.domain.identity import relation_is_currently_effective


NOW = datetime(2026, 9, 16, 12, 0, tzinfo=UTC)


def test_active_open_ended_relation_is_effective() -> None:
    assert relation_is_currently_effective(
        status="ACTIVE",
        valid_from=NOW - timedelta(days=1),
        valid_to=None,
        now=NOW,
    )


def test_future_valid_from_is_not_effective() -> None:
    assert not relation_is_currently_effective(
        status="ACTIVE",
        valid_from=NOW + timedelta(days=1),
        valid_to=None,
        now=NOW,
    )


def test_ended_valid_to_is_not_effective() -> None:
    assert not relation_is_currently_effective(
        status="ACTIVE",
        valid_from=NOW - timedelta(days=2),
        valid_to=NOW,
        now=NOW,
    )
    assert relation_is_currently_effective(
        status="ACTIVE",
        valid_from=NOW - timedelta(days=2),
        valid_to=NOW + timedelta(seconds=1),
        now=NOW,
    )


def test_ended_status_is_never_effective() -> None:
    assert not relation_is_currently_effective(
        status="ENDED",
        valid_from=NOW - timedelta(days=2),
        valid_to=NOW - timedelta(days=1),
        now=NOW,
    )


def test_revoked_status_is_never_effective() -> None:
    assert not relation_is_currently_effective(
        status="REVOKED",
        valid_from=NOW - timedelta(days=2),
        valid_to=NOW + timedelta(days=1),
        now=NOW,
    )
