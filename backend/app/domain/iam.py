"""Pure identity, organization scope, and authorization domain objects."""

from dataclasses import dataclass
from enum import StrEnum


class ScopeType(StrEnum):
    SYSTEM = "SYSTEM"
    TENANT = "TENANT"
    PROPERTY = "PROPERTY"


@dataclass(frozen=True, slots=True)
class OrganizationScope:
    scope_type: ScopeType
    tenant_id: int | None = None
    property_id: int | None = None

    def __post_init__(self) -> None:
        if self.scope_type is ScopeType.SYSTEM:
            valid = self.tenant_id is None and self.property_id is None
        elif self.scope_type is ScopeType.TENANT:
            valid = self._positive(self.tenant_id) and self.property_id is None
        elif self.scope_type is ScopeType.PROPERTY:
            valid = self._positive(self.tenant_id) and self._positive(
                self.property_id
            )
        else:
            valid = False
        if not valid:
            raise ValueError("Invalid organization scope shape")

    @staticmethod
    def _positive(value: int | None) -> bool:
        return value is not None and not isinstance(value, bool) and value > 0

    def allows(self, target: "OrganizationScope") -> bool:
        if self.scope_type is ScopeType.SYSTEM:
            return True
        if target.scope_type is ScopeType.SYSTEM:
            return False
        if self.scope_type is ScopeType.TENANT:
            return self.tenant_id == target.tenant_id
        return (
            target.scope_type is ScopeType.PROPERTY
            and self.tenant_id == target.tenant_id
            and self.property_id == target.property_id
        )


@dataclass(frozen=True, slots=True)
class RoleGrant:
    role_code: str
    scope: OrganizationScope
    permissions: frozenset[str]


@dataclass(frozen=True, slots=True)
class Principal:
    user_id: int
    status: str
    display_name: str
    grants: tuple[RoleGrant, ...]
    auth_method: str = "unknown"

    @property
    def roles(self) -> frozenset[str]:
        return frozenset(grant.role_code for grant in self.grants)

    @property
    def permissions(self) -> frozenset[str]:
        return frozenset(
            permission
            for grant in self.grants
            for permission in grant.permissions
        )

    @property
    def scopes(self) -> frozenset[OrganizationScope]:
        return frozenset(grant.scope for grant in self.grants)

    def has_permission(
        self,
        permission_code: str,
        target_scope: OrganizationScope,
    ) -> bool:
        return any(
            permission_code in grant.permissions
            and grant.scope.allows(target_scope)
            for grant in self.grants
        )
