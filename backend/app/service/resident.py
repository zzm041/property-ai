"""Resident bind-request and house-relation use cases with atomic audit."""

from collections.abc import Callable
from datetime import datetime
from typing import TypeVar

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.errors import (
    AppError,
    AuthErrorCode,
    ErrorCode,
    IamErrorCode,
    ResidentErrorCode,
)
from app.core.time import utc_now
from app.domain.audit import AuditAction, AuditResourceType
from app.domain.iam import OrganizationScope, Principal, ScopeType
from app.domain.resident import (
    RESIDENT_BINDING_REVIEW,
    RESIDENT_RELATION_READ,
    ResidentBindStatus,
    ResidentRelationStatus,
    ResidentRelationType,
)
from app.model.identity import Role, User, UserRole
from app.model.property import House
from app.model.resident import ResidentBindRequest, ResidentHouseRelation
from app.repository.identity import IdentityRepository
from app.repository.property import PropertyRepository
from app.repository.resident import ResidentBindingRepository
from app.service.audit import AuditService
from app.service.auth import AuthService
from app.service.rbac import RbacService

T = TypeVar("T")
_PENDING_UNIQUE = frozenset({"uq_resident_bind_requests_pending_house"})
_ACTIVE_UNIQUE = frozenset({"uq_resident_house_relations_active_house"})
_HOUSE_SCOPE_FK = frozenset(
    {
        "fk_resident_bind_requests_house_scope_houses",
        "fk_resident_house_relations_house_scope_houses",
    }
)


class ResidentBindingService:
    """Own request/review transactions; repositories never commit."""

    def __init__(
        self,
        session: Session,
        repository: ResidentBindingRepository | None = None,
        property_repository: PropertyRepository | None = None,
        audit_service: AuditService | None = None,
        principal_loader: Callable[[int], Principal] | None = None,
    ) -> None:
        self._session = session
        self._repository = repository or ResidentBindingRepository(session)
        self._property_repository = property_repository or PropertyRepository(
            session
        )
        self._audit_service = audit_service or AuditService(session)
        self._principal_loader = principal_loader or AuthService(
            session,
            IdentityRepository(session),
        ).load_principal

    def create_bind_request(
        self,
        *,
        actor: Principal,
        house_id: int,
        relation_type: str,
        request_id: str,
        claimed_tenant_id: int | None = None,
        claimed_property_id: int | None = None,
    ) -> ResidentBindRequest:
        self._require_positive_id(house_id, "house_id")
        validated_type = self._validate_relation_type(relation_type)
        self._validate_request_id(request_id)
        return self._execute(
            lambda: self._create_bind_request(
                actor=actor,
                house_id=house_id,
                relation_type=validated_type,
                request_id=request_id,
                claimed_tenant_id=claimed_tenant_id,
                claimed_property_id=claimed_property_id,
            )
        )

    def list_my_bind_requests(
        self,
        *,
        actor: Principal,
        after_id: int = 0,
        limit: int = 50,
    ) -> tuple[ResidentBindRequest, ...]:
        self._require_active_actor(actor)
        return self._repository.list_bind_requests_for_user(
            user_id=actor.user_id,
            after_id=max(after_id, 0),
            limit=self._page_limit(limit),
        )

    def list_pending_bind_requests(
        self,
        *,
        actor: Principal,
        tenant_id: int,
        property_id: int,
        after_id: int = 0,
        limit: int = 50,
    ) -> tuple[ResidentBindRequest, ...]:
        self._require_positive_id(tenant_id, "tenant_id")
        self._require_positive_id(property_id, "property_id")
        current = self._reload_actor(actor)
        property_record = self._property_repository.get_property(
            tenant_id=tenant_id,
            property_id=property_id,
        )
        if property_record is None:
            raise AppError(
                code=IamErrorCode.FORBIDDEN,
                message="Permission denied",
                status_code=403,
            )
        RbacService.require_permission(
            current,
            RESIDENT_BINDING_REVIEW,
            OrganizationScope(
                ScopeType.PROPERTY,
                tenant_id=property_record.tenant_id,
                property_id=property_record.id,
            ),
        )
        return self._repository.list_pending_requests_for_property(
            tenant_id=property_record.tenant_id,
            property_id=property_record.id,
            after_id=max(after_id, 0),
            limit=self._page_limit(limit),
        )

    def approve_bind_request(
        self,
        *,
        actor: Principal,
        request_id: int,
        http_request_id: str,
    ) -> tuple[ResidentBindRequest, ResidentHouseRelation]:
        self._require_positive_id(request_id, "request_id")
        self._validate_request_id(http_request_id)
        return self._execute(
            lambda: self._review_approve(
                actor=actor,
                request_id=request_id,
                http_request_id=http_request_id,
            )
        )

    def reject_bind_request(
        self,
        *,
        actor: Principal,
        request_id: int,
        reason: str,
        http_request_id: str,
    ) -> ResidentBindRequest:
        self._require_positive_id(request_id, "request_id")
        self._validate_request_id(http_request_id)
        normalized_reason = self._required_reason(reason)
        return self._execute(
            lambda: self._review_reject(
                actor=actor,
                request_id=request_id,
                reason=normalized_reason,
                http_request_id=http_request_id,
            )
        )

    def list_my_houses(
        self,
        *,
        actor: Principal,
        after_id: int = 0,
        limit: int = 50,
        now: datetime | None = None,
    ) -> tuple[tuple[ResidentHouseRelation, House], ...]:
        self._require_active_actor(actor)
        current_time = now or utc_now()
        return self._repository.list_active_relations_for_user(
            user_id=actor.user_id,
            now=current_time,
            after_id=max(after_id, 0),
            limit=self._page_limit(limit),
        )

    def list_my_house_views(
        self,
        *,
        actor: Principal,
        after_id: int = 0,
        limit: int = 50,
    ) -> tuple[tuple[ResidentHouseRelation, House, dict[str, str]], ...]:
        """Enrich only the existing authorized page, without changing its cursor."""
        pairs = self.list_my_houses(actor=actor, after_id=after_id, limit=limit)
        labels = self._property_repository.house_address_labels(tuple(house for _, house in pairs))
        return tuple((relation, house, labels.get(house.id, {})) for relation, house in pairs)

    def get_my_house(
        self,
        *,
        actor: Principal,
        house_id: int,
        now: datetime | None = None,
    ) -> House:
        self._require_active_actor(actor)
        self._require_positive_id(house_id, "house_id")
        house = self._repository.get_active_house_for_user(
            user_id=actor.user_id,
            house_id=house_id,
            now=now or utc_now(),
        )
        if house is None:
            raise AppError(
                code=ResidentErrorCode.HOUSE_NOT_FOUND,
                message="House was not found",
                status_code=404,
            )
        return house

    def list_property_relation_history(
        self,
        *,
        actor: Principal,
        tenant_id: int,
        property_id: int,
        after_id: int = 0,
        limit: int = 50,
        house_id: int | None = None,
        user_id: int | None = None,
    ) -> tuple[tuple[ResidentHouseRelation, House, User], ...]:
        self._require_positive_id(tenant_id, "tenant_id")
        self._require_positive_id(property_id, "property_id")
        if house_id is not None:
            self._require_positive_id(house_id, "house_id")
        if user_id is not None:
            self._require_positive_id(user_id, "user_id")
        current = self._reload_actor(actor)
        property_record = self._property_repository.get_property(
            tenant_id=tenant_id,
            property_id=property_id,
        )
        if property_record is None:
            raise AppError(
                code=IamErrorCode.FORBIDDEN,
                message="Permission denied",
                status_code=403,
            )
        RbacService.require_permission(
            current,
            RESIDENT_RELATION_READ,
            OrganizationScope(
                ScopeType.PROPERTY,
                tenant_id=property_record.tenant_id,
                property_id=property_record.id,
            ),
        )
        return self._repository.list_relation_history_for_property(
            tenant_id=property_record.tenant_id,
            property_id=property_record.id,
            after_id=max(after_id, 0),
            limit=self._page_limit(limit),
            house_id=house_id,
            user_id=user_id,
        )

    def end_relation_by_id(
        self,
        *,
        actor: Principal,
        relation_id: int,
        status: str,
        request_id: str,
    ) -> ResidentHouseRelation:
        self._require_positive_id(relation_id, "relation_id")
        self._validate_request_id(request_id)
        try:
            resolved_status = ResidentRelationStatus(status)
        except ValueError as exc:
            raise AppError(
                code=ResidentErrorCode.INVALID_INPUT,
                message="status is invalid",
                status_code=422,
                details={"field": "status"},
            ) from exc
        if resolved_status is ResidentRelationStatus.ACTIVE:
            raise AppError(
                code=ResidentErrorCode.INVALID_INPUT,
                message="status is invalid",
                status_code=422,
                details={"field": "status"},
            )
        return self._execute(
            lambda: self._end_relation_by_id(
                actor=actor,
                relation_id=relation_id,
                status=resolved_status,
                request_id=request_id,
            )
        )

    def end_relation(
        self,
        relation: ResidentHouseRelation,
        *,
        ended_at: datetime | None = None,
        status: ResidentRelationStatus = ResidentRelationStatus.ENDED,
    ) -> ResidentHouseRelation:
        if relation.status != ResidentRelationStatus.ACTIVE.value:
            raise AppError(
                code=ResidentErrorCode.CONFLICT,
                message="Relation is not active",
                status_code=409,
            )
        return self._repository.end_relation(
            relation,
            ended_at=ended_at or utc_now(),
            status=status.value,
        )

    def _end_relation_by_id(
        self,
        *,
        actor: Principal,
        relation_id: int,
        status: ResidentRelationStatus,
        request_id: str,
    ) -> ResidentHouseRelation:
        current = self._reload_actor(actor)
        relation = self._repository.get_relation_for_update(relation_id)
        if relation is None:
            raise AppError(
                code=ResidentErrorCode.HOUSE_NOT_FOUND,
                message="House relation was not found",
                status_code=404,
            )
        house = self._repository.get_house_in_scope(
            tenant_id=relation.tenant_id,
            property_id=relation.property_id,
            house_id=relation.house_id,
        )
        if house is None:
            raise AppError(
                code=ResidentErrorCode.HOUSE_NOT_FOUND,
                message="House relation was not found",
                status_code=404,
            )
        RbacService.require_permission(
            current,
            RESIDENT_BINDING_REVIEW,
            OrganizationScope(
                ScopeType.PROPERTY,
                tenant_id=house.tenant_id,
                property_id=house.property_id,
            ),
        )
        ended = self.end_relation(
            relation,
            ended_at=utc_now(),
            status=status,
        )
        self._append_audit(
            action=AuditAction.RESIDENT_HOUSE_RELATION_ENDED,
            resource_type=AuditResourceType.RESIDENT_HOUSE_RELATION,
            actor_user_id=current.user_id,
            resource_id=ended.id,
            request_id=request_id,
            scope_type=ScopeType.PROPERTY,
            tenant_id=ended.tenant_id,
            property_id=ended.property_id,
            details={
                "house_id": ended.house_id,
                "relation_type": ended.relation_type,
            },
        )
        return ended

    def _create_bind_request(
        self,
        *,
        actor: Principal,
        house_id: int,
        relation_type: ResidentRelationType,
        request_id: str,
        claimed_tenant_id: int | None,
        claimed_property_id: int | None,
    ) -> ResidentBindRequest:
        current = self._reload_actor(actor)
        house = self._require_registered_house(house_id)
        self._reject_claimed_scope_mismatch(
            house,
            claimed_tenant_id=claimed_tenant_id,
            claimed_property_id=claimed_property_id,
        )
        if house.status != "ACTIVE":
            raise AppError(
                code=ResidentErrorCode.HOUSE_NOT_AVAILABLE,
                message="House is not available for binding",
                status_code=409,
            )
        # Self-application is an applicant policy, not a target-property grant.
        self._require_active_actor(current)
        now = utc_now()
        if (
            self._repository.find_active_relation(
                user_id=current.user_id,
                house_id=house.id,
                now=now,
            )
            is not None
        ):
            raise AppError(
                code=ResidentErrorCode.DUPLICATE_ACTIVE_RELATION,
                message="An active house relation already exists",
                status_code=409,
            )
        request = ResidentBindRequest(
            user_id=current.user_id,
            tenant_id=house.tenant_id,
            property_id=house.property_id,
            house_id=house.id,
            relation_type=relation_type.value,
            status=ResidentBindStatus.PENDING.value,
            requested_at=now,
        )
        try:
            self._repository.add_bind_request(request)
        except IntegrityError as exc:
            self._map_insert_error(exc, expected=_PENDING_UNIQUE)
        self._append_audit(
            action=AuditAction.RESIDENT_BIND_REQUEST_CREATED,
            resource_type=AuditResourceType.RESIDENT_BIND_REQUEST,
            actor_user_id=current.user_id,
            resource_id=request.id,
            request_id=request_id,
            scope_type=ScopeType.PROPERTY,
            tenant_id=house.tenant_id,
            property_id=house.property_id,
            details={
                "house_id": house.id,
                "relation_type": relation_type.value,
            },
        )
        return request

    def _review_approve(
        self,
        *,
        actor: Principal,
        request_id: int,
        http_request_id: str,
    ) -> tuple[ResidentBindRequest, ResidentHouseRelation]:
        current = self._reload_actor(actor)
        request = self._lock_pending_request(request_id)
        house = self._require_house_in_request_scope(request)
        RbacService.require_permission(
            current,
            RESIDENT_BINDING_REVIEW,
            OrganizationScope(
                ScopeType.PROPERTY,
                tenant_id=house.tenant_id,
                property_id=house.property_id,
            ),
        )
        now = utc_now()
        request.status = ResidentBindStatus.APPROVED.value
        request.reviewer_id = current.user_id
        request.reviewed_at = now
        request.review_reason = None
        relation = ResidentHouseRelation(
            user_id=request.user_id,
            tenant_id=house.tenant_id,
            property_id=house.property_id,
            house_id=house.id,
            relation_type=request.relation_type,
            status=ResidentRelationStatus.ACTIVE.value,
            valid_from=now,
            valid_to=None,
            bind_request_id=request.id,
        )
        try:
            self._repository.add_relation(relation)
        except IntegrityError as exc:
            self._map_insert_error(exc, expected=_ACTIVE_UNIQUE)
        self._ensure_resident_role(
            user_id=request.user_id,
            tenant_id=house.tenant_id,
            property_id=house.property_id,
        )
        self._append_audit(
            action=AuditAction.RESIDENT_BIND_REQUEST_APPROVED,
            resource_type=AuditResourceType.RESIDENT_BIND_REQUEST,
            actor_user_id=current.user_id,
            resource_id=request.id,
            request_id=http_request_id,
            scope_type=ScopeType.PROPERTY,
            tenant_id=house.tenant_id,
            property_id=house.property_id,
            details={
                "house_id": house.id,
                "relation_type": request.relation_type,
                "relation_id": relation.id,
            },
        )
        from app.service.notification import NotificationService

        NotificationService(self._session).enqueue(
            tenant_id=house.tenant_id,
            property_id=house.property_id,
            user_id=request.user_id,
            title="房屋绑定已通过",
            body="Your house binding request was approved",
            resource_type="RESIDENT_BIND_REQUEST",
            resource_id=request.id,
        )
        return request, relation

    def _ensure_resident_role(
        self,
        *,
        user_id: int,
        tenant_id: int,
        property_id: int,
    ) -> None:
        role = self._session.scalar(
            select(Role).where(Role.code == "RESIDENT").with_for_update()
        )
        if role is None:
            raise AppError(
                code=IamErrorCode.ROLE_NOT_FOUND,
                message="RESIDENT role is not configured",
                status_code=500,
            )
        existing = self._session.scalar(
            select(UserRole).where(
                UserRole.user_id == user_id,
                UserRole.role_id == role.id,
                UserRole.scope_type == ScopeType.PROPERTY.value,
                UserRole.tenant_id == tenant_id,
                UserRole.property_id == property_id,
            )
        )
        if existing is not None:
            return
        self._session.add(
            UserRole(
                user_id=user_id,
                role_id=role.id,
                scope_type=ScopeType.PROPERTY.value,
                tenant_id=tenant_id,
                property_id=property_id,
            )
        )
        self._session.flush()

    def _review_reject(
        self,
        *,
        actor: Principal,
        request_id: int,
        reason: str,
        http_request_id: str,
    ) -> ResidentBindRequest:
        current = self._reload_actor(actor)
        request = self._lock_pending_request(request_id)
        house = self._require_house_in_request_scope(request)
        RbacService.require_permission(
            current,
            RESIDENT_BINDING_REVIEW,
            OrganizationScope(
                ScopeType.PROPERTY,
                tenant_id=house.tenant_id,
                property_id=house.property_id,
            ),
        )
        now = utc_now()
        request.status = ResidentBindStatus.REJECTED.value
        request.reviewer_id = current.user_id
        request.reviewed_at = now
        request.review_reason = reason
        self._append_audit(
            action=AuditAction.RESIDENT_BIND_REQUEST_REJECTED,
            resource_type=AuditResourceType.RESIDENT_BIND_REQUEST,
            actor_user_id=current.user_id,
            resource_id=request.id,
            request_id=http_request_id,
            scope_type=ScopeType.PROPERTY,
            tenant_id=house.tenant_id,
            property_id=house.property_id,
            details={
                "house_id": house.id,
                "relation_type": request.relation_type,
            },
        )
        from app.service.notification import NotificationService

        NotificationService(self._session).enqueue(
            tenant_id=house.tenant_id,
            property_id=house.property_id,
            user_id=request.user_id,
            title="房屋绑定未通过",
            body="Your house binding request was rejected",
            resource_type="RESIDENT_BIND_REQUEST",
            resource_id=request.id,
        )
        return request

    def _lock_pending_request(self, request_id: int) -> ResidentBindRequest:
        request = self._repository.get_bind_request_for_update(request_id)
        if request is None:
            raise AppError(
                code=ResidentErrorCode.BIND_REQUEST_NOT_FOUND,
                message="Bind request was not found",
                status_code=404,
            )
        if request.status != ResidentBindStatus.PENDING.value:
            raise AppError(
                code=ResidentErrorCode.ALREADY_PROCESSED,
                message="Bind request has already been processed",
                status_code=409,
            )
        return request

    def _require_registered_house(self, house_id: int) -> House:
        house = self._repository.get_registered_house(house_id)
        if house is None:
            raise AppError(
                code=ResidentErrorCode.HOUSE_NOT_FOUND,
                message="House was not found",
                status_code=404,
            )
        return house

    def _require_house_in_request_scope(
        self,
        request: ResidentBindRequest,
    ) -> House:
        house = self._repository.get_house_in_scope(
            tenant_id=request.tenant_id,
            property_id=request.property_id,
            house_id=request.house_id,
        )
        if house is None:
            raise AppError(
                code=ResidentErrorCode.HOUSE_NOT_FOUND,
                message="House was not found",
                status_code=404,
            )
        return house

    @staticmethod
    def _reject_claimed_scope_mismatch(
        house: House,
        *,
        claimed_tenant_id: int | None,
        claimed_property_id: int | None,
    ) -> None:
        if (
            claimed_tenant_id is not None
            and claimed_tenant_id != house.tenant_id
        ) or (
            claimed_property_id is not None
            and claimed_property_id != house.property_id
        ):
            raise AppError(
                code=ResidentErrorCode.INVALID_INPUT,
                message="House does not match the claimed property scope",
                status_code=422,
            )

    def _reload_actor(self, actor: Principal) -> Principal:
        if not isinstance(actor, Principal) or actor.user_id <= 0:
            raise AppError(
                code=IamErrorCode.FORBIDDEN,
                message="Permission denied",
                status_code=403,
            )
        try:
            current = self._principal_loader(actor.user_id)
        except AppError as exc:
            raise AppError(
                code=IamErrorCode.FORBIDDEN,
                message="Permission denied",
                status_code=403,
            ) from exc
        if current.user_id != actor.user_id or current.status != "ACTIVE":
            raise AppError(
                code=IamErrorCode.FORBIDDEN,
                message="Permission denied",
                status_code=403,
            )
        return current

    @staticmethod
    def _require_active_actor(actor: Principal) -> None:
        if (
            not isinstance(actor, Principal)
            or actor.user_id <= 0
            or actor.status != "ACTIVE"
        ):
            raise AppError(
                code=AuthErrorCode.USER_DISABLED
                if getattr(actor, "status", None) not in {None, "ACTIVE"}
                else IamErrorCode.FORBIDDEN,
                message="Permission denied",
                status_code=401
                if getattr(actor, "status", None) not in {None, "ACTIVE"}
                else 403,
            )

    def create_bind_request_in_owned_transaction(
        self,
        *,
        actor: Principal,
        house_id: int,
        relation_type: str,
        request_id: str,
        claimed_tenant_id: int | None = None,
        claimed_property_id: int | None = None,
    ) -> ResidentBindRequest:
        """Compose bind-request creation into a caller-owned transaction."""

        self._require_positive_id(house_id, "house_id")
        validated_type = self._validate_relation_type(relation_type)
        self._validate_request_id(request_id)
        return self._execute(
            lambda: self._create_bind_request(
                actor=actor,
                house_id=house_id,
                relation_type=validated_type,
                request_id=request_id,
                claimed_tenant_id=claimed_tenant_id,
                claimed_property_id=claimed_property_id,
            ),
            composing=True,
        )

    def _execute(self, operation: Callable[[], T], *, composing: bool = False) -> T:
        from app.core.database import run_composing_transaction, run_owned_transaction

        try:
            runner = run_composing_transaction if composing else run_owned_transaction
            return runner(self._session, operation)
        except AppError:
            raise
        except IntegrityError as exc:
            self._map_insert_error(exc, expected=_PENDING_UNIQUE | _ACTIVE_UNIQUE)
            raise
        except SQLAlchemyError as exc:
            raise AppError(
                code=ErrorCode.INTERNAL_ERROR,
                message="Resident binding could not be completed",
                status_code=500,
            ) from exc

    def _map_insert_error(
        self,
        error: IntegrityError,
        *,
        expected: frozenset[str],
    ) -> None:
        arguments = getattr(error.orig, "args", ())
        number = arguments[0] if arguments else None
        message = str(arguments[1]).casefold() if len(arguments) > 1 else ""
        if number == 1062:
            if any(name.casefold() in message for name in _PENDING_UNIQUE & expected):
                raise AppError(
                    code=ResidentErrorCode.DUPLICATE_PENDING,
                    message="A pending bind request already exists",
                    status_code=409,
                ) from error
            if any(name.casefold() in message for name in _ACTIVE_UNIQUE & expected):
                raise AppError(
                    code=ResidentErrorCode.DUPLICATE_ACTIVE_RELATION,
                    message="An active house relation already exists",
                    status_code=409,
                ) from error
            raise AppError(
                code=ResidentErrorCode.CONFLICT,
                message="Resident binding conflicted with current data",
                status_code=409,
            ) from error
        if number == 1452 and any(
            name.casefold() in message for name in _HOUSE_SCOPE_FK
        ):
            raise AppError(
                code=ResidentErrorCode.HOUSE_NOT_FOUND,
                message="House was not found",
                status_code=404,
            ) from error
        if number == 1213:
            raise AppError(
                code=ResidentErrorCode.CONFLICT,
                message="Resident binding conflicted with current data",
                status_code=409,
            ) from error
        raise AppError(
            code=ResidentErrorCode.CONFLICT,
            message="Resident binding conflicted with current data",
            status_code=409,
        ) from error

    def _append_audit(self, **values: object) -> None:
        try:
            self._audit_service.append(**values)
        except AppError:
            raise
        except SQLAlchemyError as exc:
            raise AppError(
                code=ErrorCode.INTERNAL_ERROR,
                message="Resident binding audit could not be written",
                status_code=500,
            ) from exc

    @staticmethod
    def _validate_relation_type(value: str) -> ResidentRelationType:
        try:
            return ResidentRelationType(value)
        except ValueError as exc:
            raise AppError(
                code=ResidentErrorCode.INVALID_INPUT,
                message="relation_type is invalid",
                status_code=422,
                details={"field": "relation_type"},
            ) from exc

    @staticmethod
    def _required_reason(value: str) -> str:
        if not isinstance(value, str):
            raise AppError(
                code=ResidentErrorCode.INVALID_INPUT,
                message="reason is required",
                status_code=422,
                details={"field": "reason"},
            )
        normalized = value.strip()
        if not normalized or len(normalized) > 500:
            raise AppError(
                code=ResidentErrorCode.INVALID_INPUT,
                message="reason is invalid",
                status_code=422,
                details={"field": "reason"},
            )
        return normalized

    @staticmethod
    def _require_positive_id(value: int, field: str) -> None:
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise AppError(
                code=ResidentErrorCode.INVALID_INPUT,
                message=f"{field} must be a positive integer",
                status_code=422,
                details={"field": field},
            )

    @staticmethod
    def _validate_request_id(request_id: str) -> None:
        if (
            not isinstance(request_id, str)
            or not request_id
            or request_id != request_id.strip()
            or len(request_id) > 128
        ):
            raise AppError(
                code=ResidentErrorCode.CONFLICT,
                message="Resident binding conflicted with current data",
                status_code=409,
            )

    @staticmethod
    def _page_limit(limit: int) -> int:
        if limit < 1:
            return 1
        return min(limit, 100)
