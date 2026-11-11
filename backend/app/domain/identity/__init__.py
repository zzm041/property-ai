"""Identity domain package (phase-2 step S1).

Legacy modules app.domain.{iam,phone,resident,property} stay as thin
re-export shims until phase 2b removes them.
"""
from .models import (
    HouseStatus,
    OrganizationScope,
    Principal,
    ResidentBindStatus,
    ResidentRelationStatus,
    ResidentRelationType,
    RoleGrant,
    ScopeType,
)
from .rules import (
    PROPERTY_MANAGE,
    PROPERTY_READ,
    RESIDENT_BINDING_CREATE,
    RESIDENT_BINDING_READ,
    RESIDENT_BINDING_REVIEW,
    RESIDENT_RELATION_READ,
    _CN_MOBILE,
    _E164,
    mask_phone,
    normalize_phone,
    relation_is_currently_effective,
)

__all__ = [
    "HouseStatus",
    "OrganizationScope",
    "PROPERTY_MANAGE",
    "PROPERTY_READ",
    "Principal",
    "RESIDENT_BINDING_CREATE",
    "RESIDENT_BINDING_READ",
    "RESIDENT_BINDING_REVIEW",
    "RESIDENT_RELATION_READ",
    "ResidentBindStatus",
    "ResidentRelationStatus",
    "ResidentRelationType",
    "RoleGrant",
    "ScopeType",
    "mask_phone",
    "normalize_phone",
    "relation_is_currently_effective",
]
