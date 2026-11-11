"""Governance domain: audit policy, incident grouping, announcements, operations reads.

The historical flat modules re-export this package as compatibility shims.

This domain has no errors of its own: everything uses the app.core.errors families
instead of defining a parallel hierarchy.
"""

from .models import (
    AnnouncementAudience,
    AnnouncementReviewDecision,
    AnnouncementScopeType,
    AnnouncementStatus,
    AuditAction,
    AuditActionPolicy,
    AuditEvent,
    AuditResourceType,
    AuditSource,
    IncidentStatus,
)
from .rules import (
    AUDIT_ACTION_POLICIES,
    DEFAULT_INCIDENT_THRESHOLD,
    DEFAULT_INCIDENT_WINDOW_MINUTES,
    STATS_READ,
    can_transition_announcement,
    incident_group_key,
    is_within_incident_window,
)

__all__ = [
    "AUDIT_ACTION_POLICIES",
    "AnnouncementAudience",
    "AnnouncementReviewDecision",
    "AnnouncementScopeType",
    "AnnouncementStatus",
    "AuditAction",
    "AuditActionPolicy",
    "AuditEvent",
    "AuditResourceType",
    "AuditSource",
    "DEFAULT_INCIDENT_THRESHOLD",
    "DEFAULT_INCIDENT_WINDOW_MINUTES",
    "IncidentStatus",
    "STATS_READ",
    "can_transition_announcement",
    "incident_group_key",
    "is_within_incident_window",
]
