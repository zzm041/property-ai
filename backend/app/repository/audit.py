"""Append-only audit persistence with explicit organization-scoped reads."""

from collections.abc import Sequence

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from app.domain.identity import OrganizationScope, ScopeType
from app.model.identity import AuditLog


class AuditRepository:
    """Insert and scope-constrained read operations; never commits."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def append(self, audit_log: AuditLog) -> AuditLog:
        self._session.add(audit_log)
        self._session.flush()
        return audit_log

    def list_system_scope(
        self,
        *,
        after_id: int = 0,
        limit: int = 100,
    ) -> Sequence[AuditLog]:
        return self._list_scope(
            OrganizationScope(ScopeType.SYSTEM),
            after_id=after_id,
            limit=limit,
        )

    def list_tenant_scope(
        self,
        *,
        tenant_id: int,
        after_id: int = 0,
        limit: int = 100,
    ) -> Sequence[AuditLog]:
        return self._list_scope(
            OrganizationScope(ScopeType.TENANT, tenant_id=tenant_id),
            after_id=after_id,
            limit=limit,
        )

    def list_property_scope(
        self,
        *,
        tenant_id: int,
        property_id: int,
        after_id: int = 0,
        limit: int = 100,
    ) -> Sequence[AuditLog]:
        return self._list_scope(
            OrganizationScope(
                ScopeType.PROPERTY,
                tenant_id=tenant_id,
                property_id=property_id,
            ),
            after_id=after_id,
            limit=limit,
        )

    def list_by_request_id(
        self,
        *,
        request_id: str,
        scope: OrganizationScope,
        after_id: int = 0,
        limit: int = 100,
    ) -> Sequence[AuditLog]:
        if (
            not isinstance(request_id, str)
            or not request_id
            or len(request_id) > 128
        ):
            raise ValueError("request_id must be a non-empty string up to 128 chars")
        return self._list_scope(
            scope,
            after_id=after_id,
            limit=limit,
            request_id=request_id,
        )

    def list_resource_history(
        self,
        *,
        resource_type: str,
        resource_id: int,
        scope: OrganizationScope,
        after_id: int = 0,
        limit: int = 100,
    ) -> Sequence[AuditLog]:
        self._validate_pagination(after_id=after_id, limit=limit)
        if not resource_type or not isinstance(resource_type, str):
            raise ValueError("resource_type must be a non-empty string")
        if (
            not isinstance(resource_id, int)
            or isinstance(resource_id, bool)
            or resource_id <= 0
        ):
            raise ValueError("resource_id must be a positive integer")
        statement = select(AuditLog).where(
            AuditLog.resource_type == resource_type,
            AuditLog.resource_id == resource_id,
            AuditLog.id > after_id,
        )
        statement = self._apply_scope(statement, scope)
        return self._session.scalars(
            statement.order_by(AuditLog.id).limit(limit)
        ).all()

    def _list_scope(
        self,
        scope: OrganizationScope,
        *,
        after_id: int,
        limit: int,
        request_id: str | None = None,
    ) -> Sequence[AuditLog]:
        self._validate_pagination(after_id=after_id, limit=limit)
        statement = select(AuditLog).where(AuditLog.id > after_id)
        statement = self._apply_scope(statement, scope)
        if request_id is not None:
            statement = statement.where(AuditLog.request_id == request_id)
        return self._session.scalars(
            statement.order_by(AuditLog.id).limit(limit)
        ).all()

    @staticmethod
    def _apply_scope(
        statement: Select[tuple[AuditLog]],
        scope: OrganizationScope,
    ) -> Select[tuple[AuditLog]]:
        if scope.scope_type is ScopeType.SYSTEM:
            return statement
        if scope.scope_type is ScopeType.TENANT:
            return statement.where(AuditLog.tenant_id == scope.tenant_id)
        return statement.where(
            AuditLog.tenant_id == scope.tenant_id,
            AuditLog.property_id == scope.property_id,
        )

    @staticmethod
    def _validate_pagination(*, after_id: int, limit: int) -> None:
        if (
            not isinstance(after_id, int)
            or isinstance(after_id, bool)
            or after_id < 0
        ):
            raise ValueError("after_id must be a non-negative integer")
        if (
            not isinstance(limit, int)
            or isinstance(limit, bool)
            or not 1 <= limit <= 500
        ):
            raise ValueError("limit must be between 1 and 500")
