"""dev domain models, moved from the flat app.domain modules (pure move)."""

from dataclasses import dataclass
from app.domain.identity import ScopeType

@dataclass(frozen=True, slots=True)
class SeedCodeDefinition:
    code: str
    name: str


@dataclass(frozen=True, slots=True)
class SeedUserDefinition:
    display_name: str
    role_code: str
    scope_type: ScopeType
    # PA-196: a demo account is only usable when it can log in, so a definition may
    # carry the phone and the initial password the seed binds for it. Definitions
    # without these stay identity-only, exactly as before.
    phone_e164: str | None = None
    password: str | None = None
    # PA-196: an optional existing scope to grant on. Absent ids (or ids this
    # database does not have) fall back to the tenant/property the seed owns.
    scope_tenant_id: int | None = None
    scope_property_id: int | None = None