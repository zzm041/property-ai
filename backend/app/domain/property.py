"""Property archive permissions and stable physical-house values."""

from enum import StrEnum


class HouseStatus(StrEnum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"


PROPERTY_READ = "property:read"
PROPERTY_MANAGE = "property:manage"
