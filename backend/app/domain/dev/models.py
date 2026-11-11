"""dev domain models, moved from the flat app.domain modules (pure move)."""

from dataclasses import dataclass
from app.domain.iam import ScopeType

@dataclass(frozen=True, slots=True)
class SeedCodeDefinition:
    code: str
    name: str


@dataclass(frozen=True, slots=True)
class SeedUserDefinition:
    display_name: str
    role_code: str
    scope_type: ScopeType