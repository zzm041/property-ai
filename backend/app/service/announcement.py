"""Announcement draft, human review, and publish. AI never publishes."""

from collections.abc import Callable
from datetime import datetime

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.database import begin_owned_transaction
from app.core.errors import AnnouncementErrorCode, AppError
from app.core.time import utc_now
from app.domain.ai import (
    ANNOUNCEMENT_DRAFT,
    ANNOUNCEMENT_DRAFT_PROMPT_VERSION,
    ANNOUNCEMENT_PUBLISH,
    ANNOUNCEMENT_READ,
    AnnouncementDraftOutput,
)
from app.domain.announcement import (
    AnnouncementAudience,
    AnnouncementReviewDecision,
    AnnouncementScopeType,
    AnnouncementStatus,
    can_transition_announcement,
)
from app.domain.audit import AuditAction, AuditResourceType
from app.domain.iam import OrganizationScope, Principal, ScopeType
from app.model.ai import Announcement, AnnouncementReview, AnnouncementScope
from app.provider.factory import build_llm_provider
from app.provider.llm import LLMProvider
from app.repository.ai import AiRepository
from app.repository.resident import ResidentBindingRepository
from app.repository.work_order import WorkOrderRepository
from app.service.audit import AuditService
from app.service.rbac import RbacService


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
            scope = OrganizationScope(
                ScopeType.PROPERTY,
                tenant_id=property_record.tenant_id,
                property_id=property_record.id,
            )
            RbacService.require_permission(actor, ANNOUNCEMENT_DRAFT, scope)
            tenant_id = property_record.tenant_id
            resolved_property_id = property_record.id
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
            except (ValidationError, ValueError, Exception):
                draft_title = draft_title or "Incident notice"
                draft_content = (
                    draft_content
                    or "Property staff are investigating reported issues."
                )
        if not draft_title or not draft_content:
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
                    title=draft_title[:200],
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
                    scope_type=AnnouncementScopeType.PROPERTY.value,
                    audience=audience,
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
                details={
                    "announcement_id": record.id,
                    "status": record.status,
                    "ai_draft": bool(ai_draft),
                },
            )
            return record

    def submit_review(
        self,
        *,
        actor: Principal,
        announcement_id: int,
        request_id: str,
    ) -> Announcement:
        del request_id
        with begin_owned_transaction(self._session):
            record = self._require_manage(actor, announcement_id, ANNOUNCEMENT_DRAFT)
            self._transition(record, AnnouncementStatus.PENDING_REVIEW.value)
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
            self._ai.add_announcement_review(
                AnnouncementReview(
                    tenant_id=record.tenant_id,
                    property_id=record.property_id,
                    announcement_id=record.id,
                    reviewer_user_id=actor.user_id,
                    decision=parsed.value,
                    reason=reason,
                )
            )
            record.reviewed_by_user_id = actor.user_id
            if parsed is AnnouncementReviewDecision.REJECT:
                self._transition(record, AnnouncementStatus.REJECTED.value)
                return record
            self._transition(record, AnnouncementStatus.PUBLISHED.value)
            record.published_by_user_id = actor.user_id
            record.published_at = self._clock()
            from app.service.notification import NotificationService

            notifier = NotificationService(self._session)
            for user_id in self._residents.list_active_resident_user_ids(
                tenant_id=record.tenant_id,
                property_id=record.property_id,
                now=self._clock(),
            ):
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
                details={"announcement_id": record.id, "status": record.status},
            )
            return record

    def list_published(
        self,
        *,
        actor: Principal,
        property_id: int,
        after_id: int = 0,
        limit: int = 50,
    ) -> tuple[Announcement, ...]:
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
        try:
            RbacService.require_permission(actor, ANNOUNCEMENT_READ, scope)
        except AppError:
            relations = self._residents.list_active_relations_for_user(
                user_id=actor.user_id,
                now=self._clock(),
            )
            if not any(house.property_id == property_id for _, house in relations):
                raise
        return self._ai.list_published_announcements(
            tenant_id=property_record.tenant_id,
            property_id=property_record.id,
            after_id=after_id,
            limit=limit,
        )

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
        scope = OrganizationScope(
            ScopeType.PROPERTY,
            tenant_id=record.tenant_id,
            property_id=record.property_id,
        )
        RbacService.require_permission(actor, permission, scope)
        return record

    @staticmethod
    def _transition(record: Announcement, target: str) -> None:
        if not can_transition_announcement(current=record.status, target=target):
            raise AppError(
                code=AnnouncementErrorCode.INVALID_TRANSITION,
                message="Announcement transition is not allowed",
                status_code=409,
            )
        record.status = target
