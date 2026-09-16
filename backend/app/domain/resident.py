"""Pure resident-house binding enums and ownership policy."""

from datetime import datetime
from enum import StrEnum


class ResidentRelationType(StrEnum):
    OWNER = "OWNER"
    TENANT = "TENANT"
    FAMILY = "FAMILY"
    AUTHORIZED_OTHER = "AUTHORIZED_OTHER"


class ResidentBindStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class ResidentRelationStatus(StrEnum):
    ACTIVE = "ACTIVE"
    ENDED = "ENDED"
    REVOKED = "REVOKED"


RESIDENT_BINDING_CREATE = "resident_binding:create"
RESIDENT_BINDING_READ = "resident_binding:read"
RESIDENT_BINDING_REVIEW = "resident_binding:review"
RESIDENT_RELATION_READ = "resident_relation:read"


def relation_is_currently_effective(
    *,
    status: str,
    valid_from: datetime,
    valid_to: datetime | None,
    now: datetime,
) -> bool:
    if status != ResidentRelationStatus.ACTIVE.value:
        return False
    if valid_from > now:
        return False
    if valid_to is None:
        return True
    return valid_to > now
