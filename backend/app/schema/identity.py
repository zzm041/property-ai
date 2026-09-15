"""Current Principal response schemas."""

from pydantic import BaseModel

from app.domain.iam import ScopeType


class GrantedScopeData(BaseModel):
    role_code: str
    scope_type: ScopeType
    tenant_id: int | None
    property_id: int | None
    permissions: list[str]


class MeData(BaseModel):
    user_id: int
    status: str
    display_name: str
    roles: list[str]
    permissions: list[str]
    scopes: list[GrantedScopeData]
