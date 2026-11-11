"""Explicit Development Seed runtime. Never imported by application startup."""

from collections.abc import Callable
from dataclasses import dataclass
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.database import begin_owned_transaction, create_database_engine
from app.core.errors import AppError, DevSeedErrorCode
from app.core.passwords import hash_password, require_new_password, verify_password
from app.core.time import utc_now
from app.domain.governance import AuditAction, AuditResourceType, AuditSource
from app.domain.dev import (
    DEV_PROPERTY_ADDRESS,
    DEV_PROPERTY_NAME,
    DEV_PROPERTY_TIMEZONE,
    DEV_SEED_LOCK_NAME,
    DEV_TENANT_NAME,
    SEED_PERMISSIONS,
    SEED_ROLE_PERMISSIONS,
    SEED_ROLES,
    SEED_USERS,
    SeedCodeDefinition,
    SeedUserDefinition,
)
from app.domain.identity import OrganizationScope, ScopeType
from app.model.identity import Permission, Role, User, UserPassword, UserPhone
from app.model.property import Property, Tenant
from app.repository.dev_seed import DevSeedRepository, SeedUserGrantRecord
from app.service.audit import AuditService
from app.service.identity.rbac_mutation import RbacMutationService

_ALLOWED_ENVIRONMENTS = frozenset({"development", "test"})
_DUPLICATE_CONSTRAINTS = frozenset(
    {
        "uq_tenants_name",
        "uq_properties_tenant_name",
        "uq_roles_code",
        "uq_permissions_code",
    }
)


@dataclass(frozen=True, slots=True)
class DevSeedResult:
    request_id: str


def require_dev_seed_environment(settings: Settings) -> None:
    """Reject staging/production before any Engine or Session is created."""

    if settings.app_env not in _ALLOWED_ENVIRONMENTS:
        raise AppError(
            code=DevSeedErrorCode.ENVIRONMENT_FORBIDDEN,
            message="Development Seed is allowed only in development or test",
            status_code=403,
        )


def run_development_seed(
    settings: Settings,
    *,
    engine_factory=None,
    session_factory: type[Session] | None = None,
    request_id: str | None = None,
) -> DevSeedResult:
    require_dev_seed_environment(settings)
    factory = engine_factory or create_database_engine
    engine = factory(settings.active_database_url)
    lock_connection: Connection | None = None
    session: Session | None = None
    try:
        lock_connection = engine.connect().execution_options(
            isolation_level="AUTOCOMMIT"
        )
        _acquire_dev_seed_lock(
            lock_connection,
            timeout_seconds=settings.dev_seed_lock_timeout_seconds,
        )
        session_cls = session_factory or Session
        session = session_cls(
            bind=engine,
            autoflush=False,
            expire_on_commit=False,
        )
        seed_request_id = request_id or f"dev-seed-{uuid4().hex}"
        try:
            with begin_owned_transaction(session):
                return DevSeedService(session).seed(request_id=seed_request_id)
        except AppError:
            raise
        except SQLAlchemyError as exc:
            raise AppError(
                code=DevSeedErrorCode.FAILED,
                message="Development Seed could not be completed",
                status_code=500,
            ) from exc
        except Exception as exc:
            raise AppError(
                code=DevSeedErrorCode.FAILED,
                message="Development Seed could not be completed",
                status_code=500,
            ) from exc
    finally:
        if lock_connection is not None:
            try:
                lock_connection.execute(
                    text("SELECT RELEASE_LOCK(:name)"),
                    {"name": DEV_SEED_LOCK_NAME},
                )
            except Exception:
                pass
            lock_connection.close()
        if session is not None:
            session.close()
        engine.dispose()


def _acquire_dev_seed_lock(
    connection: Connection,
    *,
    timeout_seconds: int,
) -> None:
    try:
        acquired = connection.execute(
            text("SELECT GET_LOCK(:name, :timeout)"),
            {
                "name": DEV_SEED_LOCK_NAME,
                "timeout": timeout_seconds,
            },
        ).scalar()
    except SQLAlchemyError as exc:
        raise AppError(
            code=DevSeedErrorCode.LOCK_UNAVAILABLE,
            message="Development Seed lock could not be acquired",
            status_code=503,
        ) from exc
    if acquired == 1:
        return
    if acquired == 0:
        raise AppError(
            code=DevSeedErrorCode.LOCK_UNAVAILABLE,
            message="Development Seed lock wait timed out",
            status_code=409,
        )
    raise AppError(
        code=DevSeedErrorCode.LOCK_UNAVAILABLE,
        message="Development Seed lock could not be acquired",
        status_code=503,
    )


class DevSeedService:
    """Seed Task 3 foundation data inside a caller-owned transaction."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._repository = DevSeedRepository(session)
        self._audit_service = AuditService(session)
        self._rbac_mutation = RbacMutationService(session)

    def seed(self, *, request_id: str) -> DevSeedResult:
        if not self._session.in_transaction():
            raise RuntimeError("Development Seed requires an active transaction")
        tenant = self._ensure_tenant(request_id)
        property_record = self._ensure_property(tenant, request_id)
        roles = self._ensure_roles(request_id)
        permissions = self._ensure_permissions(request_id)
        self._ensure_role_permissions(roles, permissions, request_id)
        self._ensure_users(tenant, property_record, request_id)
        return DevSeedResult(request_id=request_id)

    def _ensure_tenant(self, request_id: str) -> Tenant:
        matches = self._repository.list_tenants_by_name_exact(DEV_TENANT_NAME)
        if len(matches) > 1:
            self._raise_conflict()
        if matches:
            tenant = matches[0]
            if tenant.status != "ACTIVE":
                self._raise_conflict()
            return tenant
        tenant = Tenant(name=DEV_TENANT_NAME, status="ACTIVE")
        self._add_record(lambda: self._repository.add_tenant(tenant))
        self._append_seed_audit(
            action=AuditAction.DEV_SEED_TENANT_CREATED,
            resource_type=AuditResourceType.TENANT,
            resource_id=tenant.id,
            request_id=request_id,
            scope_type=ScopeType.TENANT,
            tenant_id=tenant.id,
            property_id=None,
            details={"seed_key": DEV_TENANT_NAME, "source": AuditSource.DEVELOPMENT_SEED.value},
        )
        return tenant

    def _ensure_property(self, tenant: Tenant, request_id: str) -> Property:
        matches = self._repository.list_properties_by_name_exact(
            tenant_id=tenant.id,
            name=DEV_PROPERTY_NAME,
        )
        if len(matches) > 1:
            self._raise_conflict()
        if matches:
            return matches[0]
        property_record = Property(
            tenant_id=tenant.id,
            name=DEV_PROPERTY_NAME,
            address=DEV_PROPERTY_ADDRESS,
            timezone=DEV_PROPERTY_TIMEZONE,
        )
        self._add_record(
            lambda: self._repository.add_property(property_record)
        )
        self._append_seed_audit(
            action=AuditAction.DEV_SEED_PROPERTY_CREATED,
            resource_type=AuditResourceType.PROPERTY,
            resource_id=property_record.id,
            request_id=request_id,
            scope_type=ScopeType.PROPERTY,
            tenant_id=tenant.id,
            property_id=property_record.id,
            details={
                "seed_key": DEV_PROPERTY_NAME,
                "source": AuditSource.DEVELOPMENT_SEED.value,
            },
        )
        return property_record

    def _ensure_roles(self, request_id: str) -> dict[str, Role]:
        roles: dict[str, Role] = {}
        for definition in SEED_ROLES:
            roles[definition.code] = self._ensure_role(definition, request_id)
        return roles

    def _ensure_role(
        self,
        definition: SeedCodeDefinition,
        request_id: str,
    ) -> Role:
        matches = self._repository.list_roles_by_code_exact(definition.code)
        if len(matches) > 1:
            self._raise_conflict()
        if matches:
            return matches[0]
        role = Role(code=definition.code, name=definition.name)
        self._add_record(lambda: self._repository.add_role(role))
        self._append_seed_audit(
            action=AuditAction.DEV_SEED_ROLE_CREATED,
            resource_type=AuditResourceType.ROLE,
            resource_id=role.id,
            request_id=request_id,
            scope_type=ScopeType.SYSTEM,
            tenant_id=None,
            property_id=None,
            details={
                "role_code": role.code,
                "source": AuditSource.DEVELOPMENT_SEED.value,
            },
        )
        return role

    def _ensure_permissions(self, request_id: str) -> dict[str, Permission]:
        permissions: dict[str, Permission] = {}
        for definition in SEED_PERMISSIONS:
            permissions[definition.code] = self._ensure_permission(
                definition,
                request_id,
            )
        return permissions

    def _ensure_permission(
        self,
        definition: SeedCodeDefinition,
        request_id: str,
    ) -> Permission:
        matches = self._repository.list_permissions_by_code_exact(definition.code)
        if len(matches) > 1:
            self._raise_conflict()
        if matches:
            return matches[0]
        permission = Permission(code=definition.code, name=definition.name)
        self._add_record(lambda: self._repository.add_permission(permission))
        self._append_seed_audit(
            action=AuditAction.DEV_SEED_PERMISSION_CREATED,
            resource_type=AuditResourceType.PERMISSION,
            resource_id=permission.id,
            request_id=request_id,
            scope_type=ScopeType.SYSTEM,
            tenant_id=None,
            property_id=None,
            details={
                "permission_code": permission.code,
                "source": AuditSource.DEVELOPMENT_SEED.value,
            },
        )
        return permission

    def _ensure_role_permissions(
        self,
        roles: dict[str, Role],
        permissions: dict[str, Permission],
        request_id: str,
    ) -> None:
        for role_code, permission_codes in SEED_ROLE_PERMISSIONS.items():
            role = roles[role_code]
            for permission_code in permission_codes:
                permission = permissions[permission_code]
                existing = self._repository.find_role_permission(
                    role_id=role.id,
                    permission_id=permission.id,
                )
                if existing is not None:
                    continue
                self._rbac_mutation.grant_role_permission_for_development_seed(
                    role_code=role.code,
                    permission_code=permission.code,
                    request_id=request_id,
                )

    def _ensure_users(
        self,
        tenant: Tenant,
        property_record: Property,
        request_id: str,
    ) -> None:
        for definition in SEED_USERS:
            self._ensure_user(
                definition,
                tenant,
                property_record,
                request_id,
            )

    def _ensure_user(
        self,
        definition: SeedUserDefinition,
        tenant: Tenant,
        property_record: Property,
        request_id: str,
    ) -> None:
        matches = self._repository.list_users_by_display_name_exact(
            definition.display_name
        )
        if len(matches) > 1:
            self._raise_conflict()
        scope = self._scope_for(definition, tenant, property_record)
        expected = SeedUserGrantRecord(
            role_code=definition.role_code,
            scope_type=definition.scope_type.value,
            tenant_id=scope.tenant_id,
            property_id=scope.property_id,
        )
        if matches:
            user = matches[0]
            if user.status != "ACTIVE":
                self._raise_conflict()
            grants = self._repository.list_user_grants(user.id)
            if grants != (expected,):
                self._raise_conflict()
            self._ensure_credentials(user, definition)
            return
        user = User(status="ACTIVE", display_name=definition.display_name)
        self._add_record(lambda: self._repository.add_user(user))
        self._append_seed_audit(
            action=AuditAction.DEV_SEED_USER_CREATED,
            resource_type=AuditResourceType.USER,
            resource_id=user.id,
            request_id=request_id,
            scope_type=ScopeType.SYSTEM,
            tenant_id=None,
            property_id=None,
            details={
                "seed_key": definition.display_name,
                "source": AuditSource.DEVELOPMENT_SEED.value,
            },
        )
        self._rbac_mutation.grant_user_role_for_development_seed(
            target_user_id=user.id,
            role_code=definition.role_code,
            scope=scope,
            request_id=request_id,
        )
        self._ensure_credentials(user, definition)

    def _ensure_credentials(
        self,
        user: User,
        definition: SeedUserDefinition,
    ) -> None:
        """PA-196: bind the demo login a definition declares, idempotently.

        A definition without a phone or password stays identity-only. When one is
        declared, a second seed run verifies the existing rows instead of writing
        new ones, and any drift (phone already owned by another user, a different
        hash) fails closed as a seed conflict rather than silently re-binding.
        """

        phone_e164 = definition.phone_e164
        password = definition.password
        if phone_e164 is None and password is None:
            return
        if phone_e164 is None or password is None:
            self._raise_conflict()
        require_new_password(password)

        owner = self._repository.find_phone_owner(phone_e164)
        existing_phone = self._repository.get_active_phone(user.id)
        if owner is not None and owner.user_id != user.id:
            self._raise_conflict()
        if existing_phone is None:
            if owner is not None:
                self._raise_conflict()
            self._add_record(
                lambda: self._repository.add_phone(
                    UserPhone(
                        user_id=user.id,
                        phone_e164=phone_e164,
                        status="ACTIVE",
                        verified_at=utc_now(),
                    )
                )
            )
        elif existing_phone.phone_e164 != phone_e164:
            self._raise_conflict()

        stored = self._repository.get_password(user.id)
        if stored is None:
            self._add_record(
                lambda: self._repository.add_password(
                    UserPassword(
                        user_id=user.id,
                        password_hash=hash_password(password),
                        password_algo="argon2id",
                    )
                )
            )
            return
        if not verify_password(stored.password_hash, password):
            self._raise_conflict()

    def _scope_for(
        self,
        definition: SeedUserDefinition,
        tenant: Tenant,
        property_record: Property,
    ) -> OrganizationScope:
        if definition.scope_type is ScopeType.SYSTEM:
            return OrganizationScope(ScopeType.SYSTEM)
        if definition.scope_type is ScopeType.TENANT:
            return OrganizationScope(ScopeType.TENANT, tenant_id=tenant.id)
        declared = self._declared_property_scope(definition)
        if declared is not None:
            return declared
        return OrganizationScope(
            ScopeType.PROPERTY,
            tenant_id=tenant.id,
            property_id=property_record.id,
        )

    def _declared_property_scope(
        self,
        definition: SeedUserDefinition,
    ) -> OrganizationScope | None:
        """PA-196: honour an explicit tenant/property scope when both rows exist.

        A database without those rows keeps seeding against its own dev property, so
        the same definition works on a fresh test database and on the delivery check
        database that actually holds the demo work orders.
        """

        tenant_id = definition.scope_tenant_id
        property_id = definition.scope_property_id
        if tenant_id is None or property_id is None:
            return None
        tenant = self._repository.get_tenant(tenant_id)
        property_record = self._repository.get_property(property_id)
        if tenant is None or property_record is None:
            return None
        if property_record.tenant_id != tenant.id:
            return None
        return OrganizationScope(
            ScopeType.PROPERTY,
            tenant_id=tenant.id,
            property_id=property_record.id,
        )

    def _append_seed_audit(self, **values: object) -> None:
        self._audit_service.append(
            actor_user_id=None,
            **values,
        )

    def _add_record(self, operation: Callable[[], object]) -> object:
        try:
            return operation()
        except IntegrityError as exc:
            if self._is_duplicate(exc):
                self._raise_conflict(cause=exc)
            raise AppError(
                code=DevSeedErrorCode.FAILED,
                message="Development Seed could not be completed",
                status_code=500,
            ) from exc

    @staticmethod
    def _is_duplicate(error: IntegrityError) -> bool:
        arguments = getattr(error.orig, "args", ())
        if not arguments or arguments[0] != 1062:
            return False
        message = str(arguments[1]).casefold() if len(arguments) > 1 else ""
        return any(name.casefold() in message for name in _DUPLICATE_CONSTRAINTS)

    @staticmethod
    def _raise_conflict(*, cause: Exception | None = None) -> None:
        error = AppError(
            code=DevSeedErrorCode.DATA_CONFLICT,
            message="Development Seed data conflicted with existing records",
            status_code=409,
        )
        if cause is not None:
            raise error from cause
        raise error
