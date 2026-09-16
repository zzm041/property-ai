"""Incident aggregation policy. Rules own grouping; AI only drafts summaries."""

from datetime import datetime, timedelta
from enum import StrEnum


class IncidentStatus(StrEnum):
    OPEN = "OPEN"
    MONITORING = "MONITORING"
    RESOLVED = "RESOLVED"
    CLOSED = "CLOSED"


DEFAULT_INCIDENT_WINDOW_MINUTES = 10
DEFAULT_INCIDENT_THRESHOLD = 5


def incident_group_key(
    *,
    tenant_id: int,
    property_id: int,
    building_id: int | None,
    category: str,
    subcategory: str | None,
) -> tuple[int, int, int | None, str, str]:
    return (tenant_id, property_id, building_id, category, subcategory or "")


def is_within_incident_window(
    *,
    created_at: datetime,
    anchor: datetime,
    window_minutes: int = DEFAULT_INCIDENT_WINDOW_MINUTES,
) -> bool:
    delta = abs(anchor - created_at)
    return delta <= timedelta(minutes=window_minutes)
