"""Task 7 permission matrix: SYSTEM_ADMIN is not auto-granted."""

from app.domain.ai import (
    AI_ANALYSIS_READ,
    AI_ANALYSIS_REVIEW,
    ANNOUNCEMENT_DRAFT,
    ANNOUNCEMENT_PUBLISH,
    ANNOUNCEMENT_READ,
    DISPATCH_RECOMMENDATION_READ,
    INCIDENT_MANAGE,
    INCIDENT_READ,
)
from app.domain.dev_seed import SEED_ROLE_PERMISSIONS


def test_system_admin_has_no_task_7_business_permissions() -> None:
    codes = set(SEED_ROLE_PERMISSIONS["SYSTEM_ADMIN"])
    forbidden = {
        AI_ANALYSIS_READ,
        AI_ANALYSIS_REVIEW,
        DISPATCH_RECOMMENDATION_READ,
        INCIDENT_READ,
        INCIDENT_MANAGE,
        ANNOUNCEMENT_READ,
        ANNOUNCEMENT_DRAFT,
        ANNOUNCEMENT_PUBLISH,
    }
    assert codes.isdisjoint(forbidden)


def test_supervisor_can_draft_but_not_publish() -> None:
    codes = set(SEED_ROLE_PERMISSIONS["SUPERVISOR"])
    assert ANNOUNCEMENT_DRAFT in codes
    assert ANNOUNCEMENT_PUBLISH not in codes
    assert INCIDENT_MANAGE in codes


def test_property_admin_can_publish() -> None:
    codes = set(SEED_ROLE_PERMISSIONS["PROPERTY_ADMIN"])
    assert ANNOUNCEMENT_PUBLISH in codes
    assert AI_ANALYSIS_REVIEW in codes
