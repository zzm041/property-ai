"""Pure employee assignment and attendance policy."""

from datetime import datetime
from enum import StrEnum


class EmployeeStatus(StrEnum):
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"


class EmployeePropertyStatus(StrEnum):
    ACTIVE = "ACTIVE"
    ENDED = "ENDED"
    REVOKED = "REVOKED"


class AttendanceOnDuty(StrEnum):
    ON_DUTY = "ON_DUTY"
    OFF_DUTY = "OFF_DUTY"
    LEAVE = "LEAVE"


class AttendanceSource(StrEnum):
    STAFF_SELF = "STAFF_SELF"
    INTERNAL = "INTERNAL"


EMPLOYEE_READ = "employee:read"
EMPLOYEE_MANAGE = "employee:manage"
ATTENDANCE_CHECK_IN = "attendance:check_in"
ATTENDANCE_CHECK_OUT = "attendance:check_out"
ATTENDANCE_READ = "attendance:read"

# Scoped STAFF or above; role membership never replaces live service,
# attendance, capability or capacity checks.
DISPATCH_STAFF_ROLES = frozenset({"STAFF", "SUPERVISOR", "PROPERTY_ADMIN", "TENANT_ADMIN"})

DEV_DUTY_CODES = (
    ("MAINTENANCE", "Maintenance"),
    ("CLEANING", "Cleaning"),
    ("SECURITY", "Security"),
)
DEV_SKILL_CODES = (
    ("ELECTRICAL", "Electrical"),
    ("PLUMBING", "Plumbing"),
    ("HVAC", "HVAC"),
)


def assignment_is_currently_effective(
    *,
    status: str,
    valid_from: datetime,
    valid_to: datetime | None,
    now: datetime,
) -> bool:
    if status != EmployeePropertyStatus.ACTIVE.value:
        return False
    if valid_from > now:
        return False
    if valid_to is None:
        return True
    return valid_to > now
