"""Announcement draft and human-publish policy."""

from enum import StrEnum


class AnnouncementStatus(StrEnum):
    DRAFT = "DRAFT"
    PENDING_REVIEW = "PENDING_REVIEW"
    PUBLISHED = "PUBLISHED"
    ARCHIVED = "ARCHIVED"
    REJECTED = "REJECTED"


class AnnouncementScopeType(StrEnum):
    TENANT = "TENANT"
    PROPERTY = "PROPERTY"
    BUILDING = "BUILDING"
    UNIT = "UNIT"
    AUDIENCE = "AUDIENCE"


class AnnouncementAudience(StrEnum):
    RESIDENT = "RESIDENT"
    STAFF = "STAFF"


class AnnouncementReviewDecision(StrEnum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"


def can_transition_announcement(*, current: str, target: str) -> bool:
    allowed = {
        AnnouncementStatus.DRAFT.value: {
            AnnouncementStatus.PENDING_REVIEW.value,
            AnnouncementStatus.REJECTED.value,
        },
        AnnouncementStatus.PENDING_REVIEW.value: {
            AnnouncementStatus.PUBLISHED.value,
            AnnouncementStatus.REJECTED.value,
            AnnouncementStatus.DRAFT.value,
        },
        AnnouncementStatus.PUBLISHED.value: {AnnouncementStatus.ARCHIVED.value},
        AnnouncementStatus.REJECTED.value: {AnnouncementStatus.DRAFT.value},
        AnnouncementStatus.ARCHIVED.value: set(),
    }
    return target in allowed.get(current, set())
