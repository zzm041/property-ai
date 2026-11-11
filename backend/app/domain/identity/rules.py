"""Identity rules: phone normalisation, resident binding, property access.

Moved verbatim from app.domain.{phone,resident,property} (phase-2 step S1).
No domain-specific exceptions here; see app.core.errors.
"""
from __future__ import annotations
import re
from datetime import datetime
from .models import ResidentRelationStatus


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


PROPERTY_READ = "property:read"


PROPERTY_MANAGE = "property:manage"


_CN_MOBILE = re.compile(r"^1[3-9]\d{9}$")


_E164 = re.compile(r"^\+[1-9]\d{7,14}$")


def normalize_phone(raw: str) -> str | None:
    if not isinstance(raw, str):
        return None
    compact = re.sub(r"[\s\-()]", "", raw.strip())
    if compact.startswith("00"):
        compact = "+" + compact[2:]
    if _CN_MOBILE.fullmatch(compact):
        return f"+86{compact}"
    if compact.startswith("+86") and _CN_MOBILE.fullmatch(compact[3:]):
        return compact
    if _E164.fullmatch(compact):
        return compact
    return None


def mask_phone(phone_e164: str) -> str:
    digits = "".join(ch for ch in phone_e164 if ch.isdigit())
    if digits.startswith("86") and len(digits) == 13:
        digits = digits[2:]
    if len(digits) < 7:
        return "****"
    return f"{digits[:3]}****{digits[-4:]}"
