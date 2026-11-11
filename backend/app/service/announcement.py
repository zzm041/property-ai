"""Announcement draft, human review, and publish. AI never publishes."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.database import begin_owned_transaction
from app.core.errors import AnnouncementErrorCode, AppError, IamErrorCode
from app.core.logging import get_logger
from app.core.time import utc_now
from app.domain.ai import (
    ANNOUNCEMENT_DRAFT,
    ANNOUNCEMENT_DRAFT_PROMPT_VERSION,
    ANNOUNCEMENT_PUBLISH,
    ANNOUNCEMENT_READ,
    AnnouncementDraftOutput,
)
from app.domain.governance import (
    AnnouncementAudience,
    AnnouncementReviewDecision,
    AnnouncementScopeType,
    AnnouncementStatus,
    can_transition_announcement,
)
from app.domain.governance import AuditAction, AuditResourceType
from app.domain.identity import OrganizationScope, Principal, ScopeType
from app.model.ai import Announcement, AnnouncementReview, AnnouncementScope
from app.model.identity import AuditLog
from app.provider.errors import ProviderError
from app.provider.factory import build_llm_provider
from app.provider.llm import LLMProvider
from app.repository.ai import AiRepository
from app.repository.audit import AuditRepository
from app.repository.employee import EmployeeRepository
from app.repository.property import PropertyRepository
from app.repository.resident import ResidentBindingRepository
from app.repository.work_order import WorkOrderRepository
from app.service.audit import AuditService
from app.service.identity.rbac import RbacService


@dataclass(frozen=True, slots=True)
class AnnouncementTarget:
    scope_type: str
    building_id: int | None
    unit_id: int | None
    audience: str


AnnouncementDetail = tuple[
    Announcement,
    tuple[AnnouncementScope, ...],
    tuple[AnnouncementReview, ...],
    tuple[AuditLog, ...],
]


logger = get_logger(__name__)


class AnnouncementService:
    def __init__(
        self,
        session: Session,
        *,
        settings: Settings | None = None,
        llm: LLMProvider | None = None,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._session = session
        self._settings = settings or get_settings()
        self._llm = llm or build_llm_provider(self._settings)
        self._clock = clock
        self._ai = AiRepository(session)
        self._audit_repository = AuditRepository(session)
        self._employees = EmployeeRepository(session)
        self._properties = PropertyRepository(session)
        self._work_orders = WorkOrderRepository(session)
        self._residents = ResidentBindingRepository(session)
        self._audit = AuditService(session)

    def create_draft(
        self,
        *,
        actor: Principal,
        property_id: int,
        request_id: str,
        title: str | None = None,
        content: str | None = None,
        incident_id: int | None = None,
        ai_draft: bool = False,
        claimed_tenant_id: int | None = None,
        scope_type: str = AnnouncementScopeType.PROPERTY.value,
        building_id: int | None = None,
        unit_id: int | None = None,
        audience: str = AnnouncementAudience.RESIDENT.value,
    ) -> Announcement:
        with begin_owned_transaction(self._session):
            property_record = self._work_orders.get_property(property_id)
            if property_record is None:
                raise AppError(
                    code=AnnouncementErrorCode.NOT_FOUND,
                    message="Announcement target was not found",
                    status_code=404,
                )
            if (
                claimed_tenant_id is not None
                and claimed_tenant_id != property_record.tenant_id
            ):
                raise AppError(
                    code=AnnouncementErrorCode.SCOPE_MISMATCH,
                    message="Declared scope does not match the property",
                    status_code=422,
                )
            tenant_id = property_record.tenant_id
            resolved_property_id = property_record.id
            target = self._resolve_target(
                actor=actor,
                permission=ANNOUNCEMENT_DRAFT,
                tenant_id=tenant_id,
                property_id=resolved_property_id,
                scope_type=scope_type,
                building_id=building_id,
                unit_id=unit_id,
                audience=audience,
            )
            resolved_incident_id = None
            if incident_id is not None:
                incident = self._ai.get_incident(incident_id)
                if (
                    incident is None
                    or incident.property_id != resolved_property_id
                    or incident.tenant_id != tenant_id
                ):
                    raise AppError(
                        code=AnnouncementErrorCode.SCOPE_MISMATCH,
                        message="Incident is not in the announcement property",
                        status_code=422,
                    )
                resolved_incident_id = incident.id
        draft_title = title
        draft_content = content
        if ai_draft:
            if self._session.in_transaction():
                from app.core.database import TransactionContractError

                raise TransactionContractError(
                    "Provider calls must not hold a business transaction"
                )
            prompt = (
                f"property={resolved_property_id};incident={resolved_incident_id or ''}"
            )
            try:
                payload = self._llm.structured_completion(
                    prompt=prompt,
                    prompt_version=ANNOUNCEMENT_DRAFT_PROMPT_VERSION,
                )
                output = AnnouncementDraftOutput.model_validate(payload)
                draft_title = draft_title or output.title
                draft_content = draft_content or output.content
            except ProviderError as exc:
                # PA-123: an unusable provider is not "the model wrote something
                # odd" — it must be visible as itself, not flattened into the
                # generic notice, or operators read a broken deployment as a
                # bland announcement.
                logger.error(
                    "announcement_draft_provider_failed",
                    error_code=exc.error_code,
                    error=type(exc).__name__,
                    incident_id=resolved_incident_id,
                )
                raise AppError(
                    code=AnnouncementErrorCode.DRAFT_PROVIDER_FAILED,
                    message="Announcement draft provider is unavailable",
                    status_code=503,
                ) from exc
            except (ValidationError, ValueError) as exc:
                # The model answered, the answer is unusable: fall back to the
                # neutral notice on purpose (previous behaviour for this case).
                logger.warning(
                    "announcement_draft_unusable_output",
                    error=type(exc).__name__,
                    detail=str(exc)[:200],
                    incident_id=resolved_incident_id,
                )
                draft_title = draft_title or "Incident notice"
                draft_content = (
                    draft_content
                    or "Property staff are investigating reported issues."
                )
        draft_title = draft_title.strip() if draft_title else ""
        draft_content = draft_content.strip() if draft_content else ""
        if (
            not draft_title
            or len(draft_title) > 200
            or not draft_content
            or len(draft_content) > 4000
        ):
            raise AppError(
                code=AnnouncementErrorCode.INVALID_INPUT,
                message="Announcement title and content are required",
                status_code=422,
            )
        with begin_owned_transaction(self._session):
            record = self._ai.add_announcement(
                Announcement(
                    tenant_id=tenant_id,
                    property_id=resolved_property_id,
                    incident_id=resolved_incident_id,
                    title=draft_title,
                    content=draft_content,
                    status=AnnouncementStatus.DRAFT.value,
                    ai_draft=1 if ai_draft else 0,
                    created_by_user_id=actor.user_id,
                )
            )
            self._ai.add_announcement_scope(
                AnnouncementScope(
                    tenant_id=record.tenant_id,
                    property_id=record.property_id,
                    announcement_id=record.id,
                    scope_type=target.scope_type,
                    building_id=target.building_id,
                    unit_id=target.unit_id,
                    audience=target.audience,
                )
            )
            self._audit.append(
                action=AuditAction.ANNOUNCEMENT_DRAFT_CREATED,
                resource_type=AuditResourceType.ANNOUNCEMENT,
                actor_user_id=actor.user_id,
                resource_id=record.id,
                request_id=request_id,
                scope_type=ScopeType.PROPERTY,
                tenant_id=record.tenant_id,
                property_id=record.property_id,
                details=self._snapshot_details(record, target)
                | {"ai_draft": bool(ai_draft)},
            )
            return record

    def update_draft(
        self,
        *,
        actor: Principal,
        announcement_id: int,
        title: str,
        content: str,
        scope_type: str,
        building_id: int | None,
        unit_id: int | None,
        audience: str,
        request_id: str,
    ) -> Announcement:
        normalized_title = title.strip()
        normalized_content = content.strip()
        if (
            not normalized_title
            or len(normalized_title) > 200
            or not normalized_content
            or len(normalized_content) > 4000
        ):
            raise AppError(
                code=AnnouncementErrorCode.INVALID_INPUT,
                message="Announcement title and content are required",
                status_code=422,
            )
        with begin_owned_transaction(self._session):
            record = self._require_manage(actor, announcement_id, ANNOUNCEMENT_DRAFT)
            if record.status not in {
                AnnouncementStatus.DRAFT.value,
                AnnouncementStatus.REJECTED.value,
            }:
                raise AppError(
                    code=AnnouncementErrorCode.INVALID_TRANSITION,
                    message="Only draft or rejected announcements can be edited",
                    status_code=409,
                )
            target = self._resolve_target(
                actor=actor,
                permission=ANNOUNCEMENT_DRAFT,
                tenant_id=record.tenant_id,
                property_id=record.property_id,
                scope_type=scope_type,
                building_id=building_id,
                unit_id=unit_id,
                audience=audience,
            )
            if record.status == AnnouncementStatus.REJECTED.value:
                self._transition(record, AnnouncementStatus.DRAFT.value)
            record.title = normalized_title
            record.content = normalized_content
            record.reviewed_by_user_id = None
            self._ai.replace_announcement_scope(
                announcement_id=record.id,
                scope=AnnouncementScope(
                    tenant_id=record.tenant_id,
                    property_id=record.property_id,
                    announcement_id=record.id,
                    scope_type=target.scope_type,
                    building_id=target.building_id,
                    unit_id=target.unit_id,
                    audience=target.audience,
                ),
            )
            self._audit.append(
                action=AuditAction.ANNOUNCEMENT_UPDATED,
                resource_type=AuditResourceType.ANNOUNCEMENT,
                actor_user_id=actor.user_id,
                resource_id=record.id,
                request_id=request_id,
                scope_type=ScopeType.PROPERTY,
                tenant_id=record.tenant_id,
                property_id=record.property_id,
                details=self._snapshot_details(record, target),
            )
            return record

    def submit_review(
        self,
        *,
        actor: Principal,
        announcement_id: int,
        request_id: str,
    ) -> Announcement:
        with begin_owned_transaction(self._session):
            record = self._require_manage(actor, announcement_id, ANNOUNCEMENT_DRAFT)
            self._transition(record, AnnouncementStatus.PENDING_REVIEW.value)
            target = self._require_single_target(record.id)
            self._audit.append(
                action=AuditAction.ANNOUNCEMENT_SUBMITTED,
                resource_type=AuditResourceType.ANNOUNCEMENT,
                actor_user_id=actor.user_id,
                resource_id=record.id,
                request_id=request_id,
                scope_type=ScopeType.PROPERTY,
                tenant_id=record.tenant_id,
                property_id=record.property_id,
                details=self._snapshot_details(record, target),
            )
            return record

    def publish(
        self,
        *,
        actor: Principal,
        announcement_id: int,
        request_id: str,
        decision: str = AnnouncementReviewDecision.APPROVE.value,
        reason: str | None = None,
    ) -> Announcement:
        with begin_owned_transaction(self._session):
            record = self._require_manage(actor, announcement_id, ANNOUNCEMENT_PUBLISH)
            if record.status != AnnouncementStatus.PENDING_REVIEW.value:
                raise AppError(
                    code=AnnouncementErrorCode.NOT_REVIEWED,
                    message="Announcement has not been reviewed",
                    status_code=409,
                )
            try:
                parsed = AnnouncementReviewDecision(decision)
            except ValueError as exc:
                raise AppError(
                    code=AnnouncementErrorCode.INVALID_INPUT,
                    message="Review decision is invalid",
                    status_code=422,
                ) from exc
            normalized_reason = reason.strip() if reason else None
            if parsed is AnnouncementReviewDecision.REJECT and (
                not normalized_reason or len(normalized_reason) > 255
            ):
                raise AppError(
                    code=AnnouncementErrorCode.INVALID_INPUT,
                    message="A rejection reason is required",
                    status_code=422,
                )
            target = self._require_single_target(record.id)
            self._ai.add_announcement_review(
                AnnouncementReview(
                    tenant_id=record.tenant_id,
                    property_id=record.property_id,
                    announcement_id=record.id,
                    reviewer_user_id=actor.user_id,
                    decision=parsed.value,
                    reason=normalized_reason,
                )
            )
            record.reviewed_by_user_id = actor.user_id
            if parsed is AnnouncementReviewDecision.REJECT:
                self._transition(record, AnnouncementStatus.REJECTED.value)
                self._audit.append(
                    action=AuditAction.ANNOUNCEMENT_REJECTED,
                    resource_type=AuditResourceType.ANNOUNCEMENT,
                    actor_user_id=actor.user_id,
                    resource_id=record.id,
                    request_id=request_id,
                    scope_type=ScopeType.PROPERTY,
                    tenant_id=record.tenant_id,
                    property_id=record.property_id,
                    details=self._snapshot_details(record, target)
                    | {"reason": normalized_reason},
                )
                return record
            self._transition(record, AnnouncementStatus.PUBLISHED.value)
            record.published_by_user_id = actor.user_id
            record.published_at = self._clock()
            from app.service.notification import NotificationService

            notifier = NotificationService(self._session)
            for user_id in self._target_user_ids(record=record, target=target):
                notifier.enqueue(
                    tenant_id=record.tenant_id,
                    property_id=record.property_id,
                    user_id=user_id,
                    title=record.title[:200],
                    body="Announcement published",
                    resource_type="ANNOUNCEMENT",
                    resource_id=record.id,
                )
            self._audit.append(
                action=AuditAction.ANNOUNCEMENT_PUBLISHED,
                resource_type=AuditResourceType.ANNOUNCEMENT,
                actor_user_id=actor.user_id,
                resource_id=record.id,
                request_id=request_id,
                scope_type=ScopeType.PROPERTY,
                tenant_id=record.tenant_id,
                property_id=record.property_id,
                details=self._snapshot_details(record, target),
            )
            return record

    def list_published(
        self,
        *,
        actor: Principal,
        property_id: int,
        after_id: int = 0,
        limit: int = 50,
        audience: str = "resident",
    ) -> tuple[Announcement, ...]:
        if audience not in {"resident", "property"}:
            raise AppError(code=AnnouncementErrorCode.INVALID_INPUT, message="Invalid audience", status_code=422)
        if actor.status != "ACTIVE":
            raise AppError(code=IamErrorCode.FORBIDDEN, message="Permission denied", status_code=403)
        property_record = self._work_orders.get_property(property_id)
        if property_record is None:
            raise AppError(
                code=AnnouncementErrorCode.NOT_FOUND,
                message="Announcement was not found",
                status_code=404,
            )
        scope = OrganizationScope(
            ScopeType.PROPERTY,
            tenant_id=property_record.tenant_id,
            property_id=property_record.id,
        )
        RbacService.require_permission(actor, ANNOUNCEMENT_READ, scope)
        now = self._clock()
        relations = tuple(
            (relation, house)
            for relation, house in self._residents.list_active_relations_for_user(
                user_id=actor.user_id,
                now=now,
                limit=500,
            )
            if house.property_id == property_id and house.tenant_id == property_record.tenant_id
        )
        employee = self._employees.get_employee_for_user(actor.user_id)
        staff_in_property = bool(
            employee is not None
            and employee.status == "ACTIVE"
            and employee.tenant_id == property_record.tenant_id
            and any(grant.role_code in {"STAFF", "SUPERVISOR", "PROPERTY_ADMIN", "TENANT_ADMIN"}
                    and ANNOUNCEMENT_READ in grant.permissions and grant.scope.allows(scope)
                    for grant in actor.grants)
            and self._employees.find_effective_assignment(
                employee_id=employee.id,
                property_id=property_id,
                now=now,
            )
        )
        manager_roles = {"SUPERVISOR", "PROPERTY_ADMIN", "TENANT_ADMIN"}
        manager_read = any(
            grant.role_code in manager_roles
            and ANNOUNCEMENT_READ in grant.permissions
            and grant.scope.allows(scope)
            for grant in actor.grants
        )
        if audience == "property" and not (manager_read or staff_in_property):
            raise AppError(code=IamErrorCode.FORBIDDEN, message="Permission denied", status_code=403)
        audiences: set[str] = set()
        building_ids: set[int] = set()
        unit_ids: set[int] = set()
        for relation, house in relations:
            audiences.add(AnnouncementAudience.RESIDENT.value)
            if relation.relation_type == "OWNER":
                audiences.add(AnnouncementAudience.OWNER.value)
            if relation.relation_type == "TENANT":
                audiences.add(AnnouncementAudience.TENANT.value)
            unit = self._properties.get_unit(
                tenant_id=house.tenant_id,
                property_id=house.property_id,
                unit_id=house.unit_id,
            )
            if unit is not None:
                building_ids.add(unit.building_id)
                unit_ids.add(unit.id)
        if audience == "property" and staff_in_property:
            audiences.add(AnnouncementAudience.STAFF.value)
        if audience == "property" and manager_read:
            audiences.update(item.value for item in AnnouncementAudience)
        if not audiences:
            raise AppError(
                code=IamErrorCode.FORBIDDEN,
                message="Permission denied",
                status_code=403,
            )
        return self._ai.list_published_announcements(
            tenant_id=property_record.tenant_id,
            property_id=property_record.id,
            building_ids=tuple(sorted(building_ids)),
            unit_ids=tuple(sorted(unit_ids)),
            audiences=tuple(sorted(audiences)),
            after_id=after_id,
            limit=limit,
        )

    def list_managed(
        self,
        *,
        actor: Principal,
        property_id: int,
        after_id: int = 0,
        limit: int = 50,
    ) -> tuple[AnnouncementDetail, ...]:
        property_record = self._work_orders.get_property(property_id)
        if property_record is None:
            raise AppError(
                code=AnnouncementErrorCode.NOT_FOUND,
                message="Announcement target was not found",
                status_code=404,
            )
        target_scope = OrganizationScope(
            ScopeType.PROPERTY,
            tenant_id=property_record.tenant_id,
            property_id=property_record.id,
        )
        tenant_scope = OrganizationScope(
            ScopeType.TENANT,
            tenant_id=property_record.tenant_id,
        )
        can_property = any(
            actor.has_permission(permission, target_scope)
            for permission in (ANNOUNCEMENT_DRAFT, ANNOUNCEMENT_PUBLISH)
        )
        can_tenant = any(
            actor.has_permission(permission, tenant_scope)
            for permission in (ANNOUNCEMENT_DRAFT, ANNOUNCEMENT_PUBLISH)
        )
        if not can_property and not can_tenant:
            RbacService.require_permission(actor, ANNOUNCEMENT_READ, target_scope)
        records = self._ai.list_announcements_for_management(
            tenant_id=property_record.tenant_id,
            property_id=property_record.id,
            include_tenant_targets=can_tenant,
            include_property_targets=can_property,
            after_id=after_id,
            limit=limit,
        )
        return tuple(self._announcement_detail(record) for record in records)

    def get_managed(
        self,
        *,
        actor: Principal,
        announcement_id: int,
    ) -> AnnouncementDetail:
        record = self._ai.get_announcement(announcement_id)
        if record is None:
            raise AppError(
                code=AnnouncementErrorCode.NOT_FOUND,
                message="Announcement was not found",
                status_code=404,
            )
        if not self._can_read_management_record(actor, record):
            raise AppError(
                code=IamErrorCode.FORBIDDEN,
                message="Permission denied",
                status_code=403,
            )
        return self._announcement_detail(record)

    def _require_manage(
        self,
        actor: Principal,
        announcement_id: int,
        permission: str,
    ) -> Announcement:
        record = self._ai.get_announcement_for_update(announcement_id)
        if record is None:
            raise AppError(
                code=AnnouncementErrorCode.NOT_FOUND,
                message="Announcement was not found",
                status_code=404,
            )
        target = self._require_single_target(record.id)
        scope = self._target_organization_scope(record, target)
        RbacService.require_permission(actor, permission, scope)
        return record

    def _can_read_management_record(
        self,
        actor: Principal,
        record: Announcement,
    ) -> bool:
        target = self._require_single_target(record.id)
        scope = self._target_organization_scope(record, target)
        return any(
            actor.has_permission(permission, scope)
            for permission in (
                ANNOUNCEMENT_DRAFT,
                ANNOUNCEMENT_PUBLISH,
            )
        )

    @staticmethod
    def _target_organization_scope(
        record: Announcement,
        target: AnnouncementTarget,
    ) -> OrganizationScope:
        if target.scope_type == AnnouncementScopeType.TENANT.value:
            return OrganizationScope(ScopeType.TENANT, tenant_id=record.tenant_id)
        return OrganizationScope(
            ScopeType.PROPERTY,
            tenant_id=record.tenant_id,
            property_id=record.property_id,
        )

    def _announcement_detail(self, record: Announcement) -> AnnouncementDetail:
        scope = OrganizationScope(
            ScopeType.PROPERTY,
            tenant_id=record.tenant_id,
            property_id=record.property_id,
        )
        return (
            record,
            self._ai.list_announcement_scopes(record.id),
            self._ai.list_announcement_reviews(record.id),
            tuple(
                self._audit_repository.list_resource_history(
                    resource_type=AuditResourceType.ANNOUNCEMENT.value,
                    resource_id=record.id,
                    scope=scope,
                    limit=500,
                )
            ),
        )

    def _resolve_target(
        self,
        *,
        actor: Principal,
        permission: str,
        tenant_id: int,
        property_id: int,
        scope_type: str,
        building_id: int | None,
        unit_id: int | None,
        audience: str,
    ) -> AnnouncementTarget:
        try:
            parsed_scope = AnnouncementScopeType(scope_type)
            parsed_audience = AnnouncementAudience(audience)
        except ValueError as exc:
            raise AppError(
                code=AnnouncementErrorCode.INVALID_INPUT,
                message="Announcement target is invalid",
                status_code=422,
            ) from exc

        if parsed_scope is AnnouncementScopeType.TENANT:
            permission_scope = OrganizationScope(
                ScopeType.TENANT,
                tenant_id=tenant_id,
            )
        else:
            permission_scope = OrganizationScope(
                ScopeType.PROPERTY,
                tenant_id=tenant_id,
                property_id=property_id,
            )
        RbacService.require_permission(actor, permission, permission_scope)

        resolved_building_id = None
        resolved_unit_id = None
        if parsed_scope is AnnouncementScopeType.BUILDING:
            if unit_id is not None or building_id is None:
                self._raise_invalid_target()
            building = self._properties.get_building(
                tenant_id=tenant_id,
                property_id=property_id,
                building_id=building_id,
            )
            if building is None:
                self._raise_invalid_target()
            resolved_building_id = building.id
        elif parsed_scope is AnnouncementScopeType.UNIT:
            if unit_id is None:
                self._raise_invalid_target()
            unit = self._properties.get_unit(
                tenant_id=tenant_id,
                property_id=property_id,
                unit_id=unit_id,
            )
            if unit is None or (
                building_id is not None and unit.building_id != building_id
            ):
                self._raise_invalid_target()
            resolved_building_id = unit.building_id
            resolved_unit_id = unit.id
        elif building_id is not None or unit_id is not None:
            self._raise_invalid_target()

        return AnnouncementTarget(
            scope_type=parsed_scope.value,
            building_id=resolved_building_id,
            unit_id=resolved_unit_id,
            audience=parsed_audience.value,
        )

    @staticmethod
    def _raise_invalid_target() -> None:
        raise AppError(
            code=AnnouncementErrorCode.SCOPE_MISMATCH,
            message="Announcement target does not match the property",
            status_code=422,
        )

    def _require_single_target(self, announcement_id: int) -> AnnouncementTarget:
        scopes = self._ai.list_announcement_scopes(announcement_id)
        if len(scopes) != 1:
            raise AppError(
                code=AnnouncementErrorCode.CONFLICT,
                message="Announcement target configuration is invalid",
                status_code=409,
            )
        scope = scopes[0]
        return AnnouncementTarget(
            scope_type=scope.scope_type,
            building_id=scope.building_id,
            unit_id=scope.unit_id,
            audience=scope.audience or AnnouncementAudience.RESIDENT.value,
        )

    @staticmethod
    def _snapshot_details(
        record: Announcement,
        target: AnnouncementTarget,
    ) -> dict[str, object]:
        return {
            "announcement_id": record.id,
            "status": record.status,
            "title": record.title,
            "content": record.content,
            "target_scope_type": target.scope_type,
            "building_id": target.building_id,
            "unit_id": target.unit_id,
            "audience": target.audience,
        }

    def _target_user_ids(
        self,
        *,
        record: Announcement,
        target: AnnouncementTarget,
    ) -> tuple[int, ...]:
        property_id = (
            None
            if target.scope_type == AnnouncementScopeType.TENANT.value
            else record.property_id
        )
        now = self._clock()
        if target.audience == AnnouncementAudience.STAFF.value:
            return tuple(
                sorted(
                    set(
                        self._employees.list_active_employee_user_ids(
                            tenant_id=record.tenant_id,
                            property_id=property_id,
                            now=now,
                        )
                    )
                )
            )

        user_ids: set[int] = set()
        for relation, house in self._residents.list_active_resident_targets(
            tenant_id=record.tenant_id,
            property_id=property_id,
            now=now,
        ):
            if (
                target.scope_type == AnnouncementScopeType.BUILDING.value
                or target.scope_type == AnnouncementScopeType.UNIT.value
            ):
                unit = self._properties.get_unit(
                    tenant_id=house.tenant_id,
                    property_id=house.property_id,
                    unit_id=house.unit_id,
                )
                if unit is None:
                    continue
                if (
                    target.scope_type == AnnouncementScopeType.BUILDING.value
                    and unit.building_id != target.building_id
                ):
                    continue
                if (
                    target.scope_type == AnnouncementScopeType.UNIT.value
                    and unit.id != target.unit_id
                ):
                    continue
            if (
                target.audience == AnnouncementAudience.OWNER.value
                and relation.relation_type != "OWNER"
            ):
                continue
            if (
                target.audience == AnnouncementAudience.TENANT.value
                and relation.relation_type != "TENANT"
            ):
                continue
            user_ids.add(relation.user_id)
        return tuple(sorted(user_ids))

    @staticmethod
    def _transition(record: Announcement, target: str) -> None:
        if not can_transition_announcement(current=record.status, target=target):
            raise AppError(
                code=AnnouncementErrorCode.INVALID_TRANSITION,
                message="Announcement transition is not allowed",
                status_code=409,
            )
        record.status = target
