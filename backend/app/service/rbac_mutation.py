"""Authorized RBAC relationship mutations with atomic append-only audit."""

from collections.abc import Callable
from dataclasses import dataclass
import re
from typing import TypeVar

from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.errors import AppError, ErrorCode, IamErrorCode
from app.domain.audit import AuditAction, AuditResourceType, AuditSource
from app.domain.iam import OrganizationScope, Principal, ScopeType
from app.model.identity import Permission, Role, RolePermission, User, UserRole
from app.repository.identity import IdentityRepository
from app.repository.property import PropertyRepository
from app.repository.rbac_mutation import RbacMutationRepository
from app.service.audit import AuditService
from app.service.auth import AuthService
from app.service.rbac import RbacService

ROLE_MANAGE_PERMISSION = "role:manage"
_ROLE_CODE_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_PERMISSION_CODE_PATTERN = re.compile(
    r"^[a-z][a-z0-9_]*:[a-z][a-z0-9_]*$"
)
_USER_ROLE_UNIQUE_CONSTRAINTS = frozenset(
    {
        "uq_user_roles_system_grant",
        "uq_user_roles_tenant_grant",
        "uq_user_roles_property_grant",
    }
)
_ROLE_PERMISSION_UNIQUE_CONSTRAINTS = frozenset(
    {"uq_role_permissions_role_permission"}
)
T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class RbacMutationResult:
    action: AuditAction
    resource_type: AuditResourceType
    relation_id: int


class RbacMutationService:
    """Own authorized mutation transactions and atomically append audit."""

    def __init__(
        self,
        session: Session,
        repository: RbacMutationRepository | None = None,
        property_repository: PropertyRepository | None = None,
        audit_service: AuditService | None = None,
        principal_loader: Callable[[int], Principal] | None = None,
    ) -> None:
        self._session = session
        self._repository = repository or RbacMutationRepository(session)
        self._property_repository = property_repository or PropertyRepository(
            session
        )
        self._audit_service = audit_service or AuditService(session)
        self._principal_loader = principal_loader or AuthService(
            session,
            IdentityRepository(session),
        ).load_principal

    def grant_user_role(
        self,
        *,
        actor: Principal,
        target_user_id: int,
        role_code: str,
        scope: OrganizationScope,
        request_id: str,
    ) -> RbacMutationResult:
        validated_scope = self._validate_scope(scope)
        self._validate_public_inputs(
            actor=actor,
            request_id=request_id,
            role_code=role_code,
            target_user_id=target_user_id,
        )
        return self._execute_authorized(
            actor=actor,
            target_scope=validated_scope,
            operation=lambda actor_user_id: (
                self._grant_user_role_in_current_transaction(
                    actor_user_id=actor_user_id,
                    target_user_id=target_user_id,
                    role_code=role_code,
                    scope=validated_scope,
                    request_id=request_id,
                )
            ),
        )

    def revoke_user_role(
        self,
        *,
        actor: Principal,
        target_user_id: int,
        role_code: str,
        scope: OrganizationScope,
        request_id: str,
    ) -> RbacMutationResult:
        validated_scope = self._validate_scope(scope)
        self._validate_public_inputs(
            actor=actor,
            request_id=request_id,
            role_code=role_code,
            target_user_id=target_user_id,
        )
        return self._execute_authorized(
            actor=actor,
            target_scope=validated_scope,
            operation=lambda actor_user_id: (
                self._revoke_user_role_in_current_transaction(
                    actor_user_id=actor_user_id,
                    target_user_id=target_user_id,
                    role_code=role_code,
                    scope=validated_scope,
                    request_id=request_id,
                )
            ),
        )

    def grant_role_permission(
        self,
        *,
        actor: Principal,
        role_code: str,
        permission_code: str,
        request_id: str,
    ) -> RbacMutationResult:
        self._validate_public_inputs(
            actor=actor,
            request_id=request_id,
            role_code=role_code,
            permission_code=permission_code,
        )
        return self._execute_authorized(
            actor=actor,
            target_scope=OrganizationScope(ScopeType.SYSTEM),
            operation=lambda actor_user_id: (
                self._grant_role_permission_in_current_transaction(
                    actor_user_id=actor_user_id,
                    role_code=role_code,
                    permission_code=permission_code,
                    request_id=request_id,
                )
            ),
        )

    def revoke_role_permission(
        self,
        *,
        actor: Principal,
        role_code: str,
        permission_code: str,
        request_id: str,
    ) -> RbacMutationResult:
        self._validate_public_inputs(
            actor=actor,
            request_id=request_id,
            role_code=role_code,
            permission_code=permission_code,
        )
        return self._execute_authorized(
            actor=actor,
            target_scope=OrganizationScope(ScopeType.SYSTEM),
            operation=lambda actor_user_id: (
                self._revoke_role_permission_in_current_transaction(
                    actor_user_id=actor_user_id,
                    role_code=role_code,
                    permission_code=permission_code,
                    request_id=request_id,
                )
            ),
        )

    def grant_user_role_for_development_seed(
        self,
        *,
        target_user_id: int,
        role_code: str,
        scope: OrganizationScope,
        request_id: str,
    ) -> RbacMutationResult:
        return self._grant_user_role_in_current_transaction(
            actor_user_id=None,
            target_user_id=target_user_id,
            role_code=role_code,
            scope=scope,
            request_id=request_id,
            source=AuditSource.DEVELOPMENT_SEED,
        )

    def grant_role_permission_for_development_seed(
        self,
        *,
        role_code: str,
        permission_code: str,
        request_id: str,
    ) -> RbacMutationResult:
        return self._grant_role_permission_in_current_transaction(
            actor_user_id=None,
            role_code=role_code,
            permission_code=permission_code,
            request_id=request_id,
            source=AuditSource.DEVELOPMENT_SEED,
        )

    def _grant_user_role_in_current_transaction(
        self,
        *,
        actor_user_id: int | None,
        target_user_id: int,
        role_code: str,
        scope: OrganizationScope,
        request_id: str,
        source: AuditSource = AuditSource.INTERNAL,
    ) -> RbacMutationResult:
        self._require_active_transaction()
        self._validate_internal_inputs(
            actor_user_id=actor_user_id,
            request_id=request_id,
            role_code=role_code,
            target_user_id=target_user_id,
            source=source,
        )
        validated_scope = self._validate_scope(scope)
        target_user = self._require_user(target_user_id)
        role = self._require_role(role_code)
        self._require_authoritative_scope(validated_scope)
        existing = self._repository.find_user_role_for_update(
            user_id=target_user.id,
            role_id=role.id,
            scope=validated_scope,
        )
        if existing is not None:
            self._raise_duplicate_grant()

        user_role = UserRole(
            user_id=target_user.id,
            role_id=role.id,
            scope_type=validated_scope.scope_type.value,
            tenant_id=validated_scope.tenant_id,
            property_id=validated_scope.property_id,
        )
        try:
            self._repository.add_user_role(user_role)
        except IntegrityError as exc:
            self._map_relation_insert_integrity_error(
                exc,
                expected_constraints=_USER_ROLE_UNIQUE_CONSTRAINTS,
            )
        self._append_user_role_audit(
            action=AuditAction.IAM_USER_ROLE_GRANTED,
            actor_user_id=actor_user_id,
            target_user_id=target_user.id,
            role_code=role.code,
            scope=validated_scope,
            relation_id=user_role.id,
            request_id=request_id,
            source=source,
        )
        return RbacMutationResult(
            action=AuditAction.IAM_USER_ROLE_GRANTED,
            resource_type=AuditResourceType.USER_ROLE,
            relation_id=user_role.id,
        )

    def _revoke_user_role_in_current_transaction(
        self,
        *,
        actor_user_id: int | None,
        target_user_id: int,
        role_code: str,
        scope: OrganizationScope,
        request_id: str,
        source: AuditSource = AuditSource.INTERNAL,
    ) -> RbacMutationResult:
        self._require_active_transaction()
        self._validate_internal_inputs(
            actor_user_id=actor_user_id,
            request_id=request_id,
            role_code=role_code,
            target_user_id=target_user_id,
            source=source,
        )
        validated_scope = self._validate_scope(scope)
        target_user = self._require_user(target_user_id)
        role = self._require_role(role_code)
        self._require_authoritative_scope(validated_scope)
        user_role = self._repository.find_user_role_for_update(
            user_id=target_user.id,
            role_id=role.id,
            scope=validated_scope,
        )
        if user_role is None:
            self._raise_grant_not_found()
        relation_id = user_role.id
        try:
            self._repository.delete_user_role(user_role)
        except IntegrityError as exc:
            self._raise_mutation_conflict(cause=exc)
        self._append_user_role_audit(
            action=AuditAction.IAM_USER_ROLE_REVOKED,
            actor_user_id=actor_user_id,
            target_user_id=target_user.id,
            role_code=role.code,
            scope=validated_scope,
            relation_id=relation_id,
            request_id=request_id,
            source=source,
        )
        return RbacMutationResult(
            action=AuditAction.IAM_USER_ROLE_REVOKED,
            resource_type=AuditResourceType.USER_ROLE,
            relation_id=relation_id,
        )

    def _grant_role_permission_in_current_transaction(
        self,
        *,
        actor_user_id: int | None,
        role_code: str,
        permission_code: str,
        request_id: str,
        source: AuditSource = AuditSource.INTERNAL,
    ) -> RbacMutationResult:
        self._require_active_transaction()
        self._validate_internal_inputs(
            actor_user_id=actor_user_id,
            request_id=request_id,
            role_code=role_code,
            permission_code=permission_code,
            source=source,
        )
        role = self._require_role(role_code)
        permission = self._require_permission(permission_code)
        existing = self._repository.find_role_permission(
            role_id=role.id,
            permission_id=permission.id,
        )
        if existing is not None:
            self._raise_duplicate_grant()

        role_permission = RolePermission(
            role_id=role.id,
            permission_id=permission.id,
        )
        try:
            self._repository.add_role_permission(role_permission)
        except IntegrityError as exc:
            self._map_relation_insert_integrity_error(
                exc,
                expected_constraints=_ROLE_PERMISSION_UNIQUE_CONSTRAINTS,
            )
        self._append_role_permission_audit(
            action=AuditAction.IAM_ROLE_PERMISSION_GRANTED,
            actor_user_id=actor_user_id,
            role_code=role.code,
            permission_code=permission.code,
            relation_id=role_permission.id,
            request_id=request_id,
            source=source,
        )
        return RbacMutationResult(
            action=AuditAction.IAM_ROLE_PERMISSION_GRANTED,
            resource_type=AuditResourceType.ROLE_PERMISSION,
            relation_id=role_permission.id,
        )

    def _revoke_role_permission_in_current_transaction(
        self,
        *,
        actor_user_id: int | None,
        role_code: str,
        permission_code: str,
        request_id: str,
        source: AuditSource = AuditSource.INTERNAL,
    ) -> RbacMutationResult:
        self._require_active_transaction()
        self._validate_internal_inputs(
            actor_user_id=actor_user_id,
            request_id=request_id,
            role_code=role_code,
            permission_code=permission_code,
            source=source,
        )
        role = self._require_role(role_code)
        permission = self._require_permission(permission_code)
        role_permission = self._repository.find_role_permission_for_update(
            role_id=role.id,
            permission_id=permission.id,
        )
        if role_permission is None:
            self._raise_grant_not_found()
        relation_id = role_permission.id
        try:
            self._repository.delete_role_permission(role_permission)
        except IntegrityError as exc:
            self._raise_mutation_conflict(cause=exc)
        self._append_role_permission_audit(
            action=AuditAction.IAM_ROLE_PERMISSION_REVOKED,
            actor_user_id=actor_user_id,
            role_code=role.code,
            permission_code=permission.code,
            relation_id=relation_id,
            request_id=request_id,
            source=source,
        )
        return RbacMutationResult(
            action=AuditAction.IAM_ROLE_PERMISSION_REVOKED,
            resource_type=AuditResourceType.ROLE_PERMISSION,
            relation_id=relation_id,
        )

    def _execute_authorized(
        self,
        *,
        actor: Principal,
        target_scope: OrganizationScope,
        operation: Callable[[int], T],
    ) -> T:
        from app.core.database import run_owned_transaction

        try:
            def _authorized() -> T:
                current_actor = self._reload_current_actor(actor.user_id)
                RbacService.require_permission(
                    current_actor,
                    ROLE_MANAGE_PERMISSION,
                    target_scope,
                )
                return operation(current_actor.user_id)

            return run_owned_transaction(self._session, _authorized)
        except AppError:
            raise
        except SQLAlchemyError as exc:
            raise AppError(
                code=ErrorCode.INTERNAL_ERROR,
                message="RBAC mutation could not be completed",
                status_code=500,
            ) from exc

    def _reload_current_actor(self, actor_user_id: int) -> Principal:
        try:
            current_actor = self._principal_loader(actor_user_id)
        except AppError as exc:
            raise AppError(
                code=IamErrorCode.FORBIDDEN,
                message="Permission denied",
                status_code=403,
            ) from exc
        if (
            current_actor.user_id != actor_user_id
            or current_actor.status != "ACTIVE"
        ):
            raise AppError(
                code=IamErrorCode.FORBIDDEN,
                message="Permission denied",
                status_code=403,
            )
        return current_actor

    def _require_user(self, user_id: int) -> User:
        peeked = self._repository.get_user(user_id)
        if peeked is None:
            raise AppError(
                code=IamErrorCode.USER_NOT_FOUND,
                message="Target user was not found",
                status_code=404,
            )
        user = self._repository.get_user_for_update(user_id)
        if user is None or user.status != "ACTIVE":
            raise AppError(
                code=IamErrorCode.FORBIDDEN,
                message="Target user is not active",
                status_code=409,
            )
        return user

    def _require_role(self, role_code: str) -> Role:
        role = self._repository.get_role_by_code(role_code)
        if role is None:
            raise AppError(
                code=IamErrorCode.ROLE_NOT_FOUND,
                message="Role was not found",
                status_code=404,
            )
        return role

    def _require_permission(self, permission_code: str) -> Permission:
        permission = self._repository.get_permission_by_code(permission_code)
        if permission is None:
            raise AppError(
                code=IamErrorCode.PERMISSION_NOT_FOUND,
                message="Permission was not found",
                status_code=404,
            )
        return permission

    def _require_authoritative_scope(self, scope: OrganizationScope) -> None:
        if scope.scope_type is ScopeType.SYSTEM:
            return
        if scope.scope_type is ScopeType.TENANT:
            tenant = self._property_repository.get_tenant(scope.tenant_id)
            if tenant is not None:
                return
        elif (
            self._property_repository.get_property(
                tenant_id=scope.tenant_id,
                property_id=scope.property_id,
            )
            is not None
        ):
            return
        raise AppError(
            code=IamErrorCode.INVALID_SCOPE,
            message="Organization scope is invalid",
            status_code=422,
        )

    def _append_user_role_audit(
        self,
        *,
        action: AuditAction,
        actor_user_id: int | None,
        target_user_id: int,
        role_code: str,
        scope: OrganizationScope,
        relation_id: int,
        request_id: str,
        source: AuditSource,
    ) -> None:
        self._append_audit(
            action=action,
            resource_type=AuditResourceType.USER_ROLE,
            actor_user_id=actor_user_id,
            resource_id=relation_id,
            request_id=request_id,
            scope_type=scope.scope_type,
            tenant_id=scope.tenant_id,
            property_id=scope.property_id,
            details={
                "target_user_id": target_user_id,
                "role_code": role_code,
                "scope_type": scope.scope_type.value,
                "source": source.value,
            },
        )

    def _append_role_permission_audit(
        self,
        *,
        action: AuditAction,
        actor_user_id: int | None,
        role_code: str,
        permission_code: str,
        relation_id: int,
        request_id: str,
        source: AuditSource,
    ) -> None:
        self._append_audit(
            action=action,
            resource_type=AuditResourceType.ROLE_PERMISSION,
            actor_user_id=actor_user_id,
            resource_id=relation_id,
            request_id=request_id,
            scope_type=ScopeType.SYSTEM,
            tenant_id=None,
            property_id=None,
            details={
                "role_code": role_code,
                "permission_code": permission_code,
                "source": source.value,
            },
        )

    def _append_audit(self, **values: object) -> None:
        try:
            self._audit_service.append(**values)
        except AppError:
            raise
        except SQLAlchemyError as exc:
            raise AppError(
                code=ErrorCode.INTERNAL_ERROR,
                message="RBAC mutation audit could not be written",
                status_code=500,
            ) from exc

    @classmethod
    def _map_relation_insert_integrity_error(
        cls,
        error: IntegrityError,
        *,
        expected_constraints: frozenset[str],
    ) -> None:
        if cls._is_expected_duplicate(error, expected_constraints):
            cls._raise_duplicate_grant(cause=error)
        cls._raise_mutation_conflict(cause=error)

    @staticmethod
    def _is_expected_duplicate(
        error: IntegrityError,
        expected_constraints: frozenset[str],
    ) -> bool:
        arguments = getattr(error.orig, "args", ())
        if not arguments or arguments[0] != 1062:
            return False
        message = str(arguments[1]).casefold() if len(arguments) > 1 else ""
        return any(name.casefold() in message for name in expected_constraints)

    def _require_active_transaction(self) -> None:
        from app.core.database import require_owned_transaction

        require_owned_transaction(self._session)

    @classmethod
    def _validate_public_inputs(
        cls,
        *,
        actor: Principal,
        request_id: str,
        role_code: str,
        target_user_id: int | None = None,
        permission_code: str | None = None,
    ) -> None:
        if not isinstance(actor, Principal) or not cls._is_positive_id(
            actor.user_id
        ):
            raise AppError(
                code=IamErrorCode.FORBIDDEN,
                message="Permission denied",
                status_code=403,
            )
        cls._validate_internal_inputs(
            actor_user_id=actor.user_id,
            request_id=request_id,
            role_code=role_code,
            target_user_id=target_user_id,
            permission_code=permission_code,
            source=AuditSource.INTERNAL,
        )

    @classmethod
    def _validate_internal_inputs(
        cls,
        *,
        actor_user_id: int | None,
        request_id: str,
        role_code: str,
        target_user_id: int | None = None,
        permission_code: str | None = None,
        source: AuditSource,
    ) -> None:
        if not isinstance(source, AuditSource):
            cls._raise_mutation_conflict()
        if source is AuditSource.INTERNAL and not cls._is_positive_id(
            actor_user_id
        ):
            cls._raise_mutation_conflict()
        if (
            source is AuditSource.DEVELOPMENT_SEED
            and actor_user_id is not None
        ):
            cls._raise_mutation_conflict()
        if target_user_id is not None and not cls._is_positive_id(
            target_user_id
        ):
            cls._raise_mutation_conflict()
        if (
            not isinstance(role_code, str)
            or not _ROLE_CODE_PATTERN.fullmatch(role_code)
        ):
            raise AppError(
                code=IamErrorCode.ROLE_NOT_FOUND,
                message="Role was not found",
                status_code=404,
            )
        if permission_code is not None and (
            not isinstance(permission_code, str)
            or not _PERMISSION_CODE_PATTERN.fullmatch(permission_code)
        ):
            raise AppError(
                code=IamErrorCode.PERMISSION_NOT_FOUND,
                message="Permission was not found",
                status_code=404,
            )
        if (
            not isinstance(request_id, str)
            or not request_id
            or request_id != request_id.strip()
            or len(request_id) > 128
            or any(
                ord(character) < 32 or ord(character) == 127
                for character in request_id
            )
        ):
            cls._raise_mutation_conflict()

    @staticmethod
    def _validate_scope(scope: OrganizationScope) -> OrganizationScope:
        try:
            if not isinstance(scope, OrganizationScope):
                raise ValueError
            return OrganizationScope(
                scope_type=ScopeType(scope.scope_type),
                tenant_id=scope.tenant_id,
                property_id=scope.property_id,
            )
        except (TypeError, ValueError) as exc:
            raise AppError(
                code=IamErrorCode.INVALID_SCOPE,
                message="Organization scope is invalid",
                status_code=422,
            ) from exc

    @staticmethod
    def _is_positive_id(value: object) -> bool:
        return isinstance(value, int) and not isinstance(value, bool) and value > 0

    @staticmethod
    def _raise_duplicate_grant(
        *,
        cause: Exception | None = None,
    ) -> None:
        error = AppError(
            code=IamErrorCode.DUPLICATE_GRANT,
            message="Authorization grant already exists",
            status_code=409,
        )
        if cause is not None:
            raise error from cause
        raise error

    @staticmethod
    def _raise_grant_not_found() -> None:
        raise AppError(
            code=IamErrorCode.GRANT_NOT_FOUND,
            message="Authorization grant was not found",
            status_code=404,
        )

    @staticmethod
    def _raise_mutation_conflict(
        *,
        cause: Exception | None = None,
    ) -> None:
        error = AppError(
            code=IamErrorCode.MUTATION_CONFLICT,
            message="RBAC mutation conflicted with current data",
            status_code=409,
        )
        if cause is not None:
            raise error from cause
        raise error
