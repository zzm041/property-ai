"""Guarded high-risk identity proof and two-reviewer workflow.

The service is inert until both the pending schema and an approved proof provider
exist. SMS possession alone is deliberately not a proof provider.
"""

from collections.abc import Callable
from datetime import datetime, timedelta
from hashlib import sha256
import json

from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.database import run_owned_transaction
from app.core.errors import AppError, IdentityRecoveryErrorCode, IamErrorCode
from app.core.passwords import hash_password, require_new_password, verify_password
from app.core.time import utc_now
from app.domain.audit import AuditAction, AuditResourceType
from app.domain.iam import OrganizationScope, Principal, ScopeType
from app.domain.phone import normalize_phone
from app.model.identity import (
    AccountMerge,
    IdentityRecoveryCase,
    IdentityRecoveryReview,
    PhoneIdentityTombstone,
    UserPassword,
    UserPhone,
)
from app.provider.identity_proof import IdentityProofProvider, UnavailableIdentityProofProvider
from app.repository.identity_recovery import IdentityCompletionRows, IdentityRecoveryRepository
from app.service.audit import AuditService
from app.service.rbac import RbacService


IDENTITY_REVIEW = "identity:review"
QUARANTINE_DAYS = 30
CASE_LIFETIME_DAYS = 60


class IdentityRecoveryService:
    def __init__(
        self,
        session: Session,
        *,
        provider: IdentityProofProvider | None = None,
        repository: IdentityRecoveryRepository | None = None,
        audit_service: AuditService | None = None,
        principal_loader: Callable[[int], Principal] | None = None,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._session = session
        self._provider = provider or UnavailableIdentityProofProvider()
        self._repository = repository or IdentityRecoveryRepository(session)
        self._audit = audit_service or AuditService(session)
        self._principal_loader = principal_loader
        self._clock = clock

    def capabilities(self) -> dict[str, object]:
        schema = self._schema_available()
        provider = self._provider.available is True
        return {
            "schema_available": schema,
            "proof_provider_available": provider,
            "enabled": schema and provider,
            "quarantine_days": QUARANTINE_DAYS,
            "required_distinct_reviewers": 2,
        }

    def create_case(
        self,
        *,
        actor: Principal,
        purpose: str,
        phone: str,
        proof_assertion: str,
        source_user_id: int | None,
        request_id: str,
    ) -> IdentityRecoveryCase:
        self._require_enabled()
        phone_e164 = normalize_phone(phone)
        if phone_e164 is None:
            self._invalid("A valid subject phone is required")
        if purpose not in {"MERGE_SOURCE_PROOF", "ACCOUNT_RECOVERY", "PHONE_REUSE"}:
            self._invalid("Recovery purpose is invalid")
        if purpose == "MERGE_SOURCE_PROOF" and source_user_id == actor.user_id:
            self._invalid("Merge source and target must be different users")
        proof = self._provider.verify(
            assertion=proof_assertion,
            purpose=purpose,
            phone_e164=phone_e164,
        )
        if not proof.verified:
            raise AppError(
                code=IdentityRecoveryErrorCode.PROOF_REJECTED,
                message="Independent identity proof was rejected",
                status_code=403,
            )
        now = self._clock()

        def operation() -> IdentityRecoveryCase:
            current = self._reload_active(actor.user_id)
            existing = self._repository.find_active_case(purpose=purpose, phone_e164=phone_e164)
            if existing is not None:
                raise AppError(
                    code=IdentityRecoveryErrorCode.CONFLICT,
                    message="An active recovery case already exists for this purpose",
                    status_code=409,
                    details={"case_id": existing.id},
                )
            historical = self._repository.latest_phone_owner(phone_e164)
            historical_owner_id = None if historical is None else historical.user_id
            if source_user_id is not None and source_user_id != historical_owner_id:
                self._invalid("Merge source must match the recorded phone ownership history")
            resolved_source = historical_owner_id
            if resolved_source is None:
                self._invalid("Historical phone ownership could not be established")
            if purpose in {"MERGE_SOURCE_PROOF", "ACCOUNT_RECOVERY"} and resolved_source == current.user_id:
                self._invalid("Merge source and target must be different users")
            record = self._repository.add_case(
                IdentityRecoveryCase(
                    purpose=purpose,
                    requester_user_id=current.user_id,
                    source_user_id=resolved_source,
                    target_user_id=current.user_id,
                    subject_phone_e164=phone_e164,
                    proof_provider=proof.provider,
                    proof_reference_hash=self._hash(proof.reference),
                    proof_result_hash=self._hash(proof.subject_reference),
                    status="PENDING_REVIEW",
                    quarantine_started_at=now,
                    quarantine_ends_at=now + timedelta(days=QUARANTINE_DAYS),
                    proof_verified_at=now,
                    expires_at=now + timedelta(days=CASE_LIFETIME_DAYS),
                    attempt_count=1,
                    version=1,
                )
            )
            self._audit.append(
                action=AuditAction.IDENTITY_RECOVERY_REQUESTED,
                resource_type=AuditResourceType.IDENTITY_RECOVERY_CASE,
                actor_user_id=current.user_id,
                resource_id=record.id,
                request_id=request_id,
                scope_type=ScopeType.SYSTEM,
                details={"purpose": purpose, "status": record.status},
            )
            return record

        try:
            return run_owned_transaction(self._session, operation)
        except AppError:
            raise
        except IntegrityError as exc:
            raise AppError(code=IdentityRecoveryErrorCode.CONFLICT, message="Recovery case conflicted", status_code=409) from exc

    def review_case(
        self,
        *,
        actor: Principal,
        case_id: int,
        decision: str,
        reason: str,
        expected_version: int,
        request_id: str,
    ) -> IdentityRecoveryCase:
        self._require_schema()
        self._require_reviewer(actor)
        if decision not in {"APPROVE", "REJECT"} or len(reason.strip()) < 2:
            self._invalid("Review decision or reason is invalid")

        def operation() -> IdentityRecoveryCase:
            current = self._reload_reviewer(actor.user_id)
            record = self._repository.get_case_for_update(case_id)
            if record is None:
                raise AppError(code=IdentityRecoveryErrorCode.NOT_FOUND, message="Recovery case was not found", status_code=404)
            if record.version != expected_version or record.status != "PENDING_REVIEW":
                raise AppError(code=IdentityRecoveryErrorCode.CONFLICT, message="Recovery case changed", status_code=409)
            if current.user_id in {record.requester_user_id, record.source_user_id, record.target_user_id}:
                raise AppError(
                    code=IamErrorCode.FORBIDDEN,
                    message="Identity case participants cannot review their own case",
                    status_code=403,
                )
            self._repository.add_review(
                IdentityRecoveryReview(case_id=record.id, reviewer_user_id=current.user_id, decision=decision, reason=reason.strip())
            )
            if decision == "REJECT":
                record.status = "REJECTED"
                record.review_completed_at = self._clock()
            record.version += 1
            self._audit.append(
                action=AuditAction.IDENTITY_RECOVERY_REVIEWED,
                resource_type=AuditResourceType.IDENTITY_RECOVERY_CASE,
                actor_user_id=current.user_id,
                resource_id=record.id,
                request_id=request_id,
                scope_type=ScopeType.SYSTEM,
                details={"decision": decision, "status": record.status},
            )
            return record

        try:
            return run_owned_transaction(self._session, operation)
        except AppError:
            raise
        except IntegrityError as exc:
            raise AppError(code=IdentityRecoveryErrorCode.CONFLICT, message="The same reviewer cannot review twice", status_code=409) from exc

    def finalize_case(
        self,
        *,
        actor: Principal,
        case_id: int,
        expected_version: int,
        request_id: str,
    ) -> IdentityRecoveryCase:
        self._require_schema()
        self._require_reviewer(actor)

        def operation() -> IdentityRecoveryCase:
            current = self._reload_reviewer(actor.user_id)
            record = self._repository.get_case_for_update(case_id)
            if record is None:
                raise AppError(code=IdentityRecoveryErrorCode.NOT_FOUND, message="Recovery case was not found", status_code=404)
            if record.version != expected_version or record.status != "PENDING_REVIEW":
                raise AppError(code=IdentityRecoveryErrorCode.CONFLICT, message="Recovery case changed", status_code=409)
            now = self._clock()
            if now < record.quarantine_ends_at:
                raise AppError(
                    code=IdentityRecoveryErrorCode.QUARANTINE_ACTIVE,
                    message="The 30-day phone quarantine is still active",
                    status_code=409,
                    details={"quarantine_ends_at": record.quarantine_ends_at.isoformat()},
                )
            if now >= record.expires_at:
                record.status = "EXPIRED"
                record.version += 1
                return record
            reviews = tuple(item for item in self._repository.list_reviews(record.id) if item.decision == "APPROVE")
            reviewers = {item.reviewer_user_id for item in reviews}
            participants = {record.requester_user_id, record.source_user_id, record.target_user_id}
            if (
                len(reviewers) < 2
                or reviewers & participants
                or not all(self._reviewer_is_current(user_id) for user_id in reviewers)
            ):
                raise AppError(code=IdentityRecoveryErrorCode.CONFLICT, message="Two distinct currently authorized approvals are required", status_code=409)
            source_user_id, target_user_id = self._case_parties(record)
            rows = self._repository.lock_completion_rows(
                source_user_id=source_user_id,
                target_user_id=target_user_id,
                phone_e164=record.subject_phone_e164,
            )
            impact = self._build_impact(record, rows)
            record.status = "APPROVED"
            record.review_completed_at = now
            record.impact_json = impact
            record.impact_hash = self._impact_hash(impact)
            record.version += 1
            self._audit.append(
                action=AuditAction.IDENTITY_RECOVERY_FINALIZED,
                resource_type=AuditResourceType.IDENTITY_RECOVERY_CASE,
                actor_user_id=current.user_id,
                resource_id=record.id,
                request_id=request_id,
                scope_type=ScopeType.SYSTEM,
                details={"purpose": record.purpose, "status": record.status, "reviewer_count": len(reviewers)},
            )
            return record

        return run_owned_transaction(self._session, operation)

    def complete_case(
        self,
        *,
        actor: Principal,
        case_id: int,
        expected_version: int,
        current_password: str | None,
        target_reauth_assertion: str | None,
        new_password: str | None,
        confirm_replace_phone: bool,
        request_id: str,
    ) -> IdentityRecoveryCase:
        """Atomically apply an approved proof case; approval alone changes no identity."""

        self._require_schema()

        def operation() -> IdentityRecoveryCase:
            current = self._reload_active(actor.user_id)
            record = self._repository.get_case_for_update(case_id)
            if record is None:
                raise AppError(code=IdentityRecoveryErrorCode.NOT_FOUND, message="Recovery case was not found", status_code=404)
            if record.requester_user_id != current.user_id or record.target_user_id != current.user_id:
                raise AppError(code=IamErrorCode.FORBIDDEN, message="Permission denied", status_code=403)
            if record.status == "COMPLETED":
                return record
            if record.version != expected_version or record.status != "APPROVED":
                raise AppError(code=IdentityRecoveryErrorCode.CONFLICT, message="Recovery case changed", status_code=409)
            now = self._clock()
            if now >= record.expires_at or record.proof_verified_at is None:
                raise AppError(code=IdentityRecoveryErrorCode.CONFLICT, message="Identity proof is expired", status_code=409)
            reviewers = {
                item.reviewer_user_id
                for item in self._repository.list_reviews(record.id)
                if item.decision == "APPROVE"
            }
            participants = {record.requester_user_id, record.source_user_id, record.target_user_id}
            if (
                len(reviewers) < 2
                or reviewers & participants
                or not all(self._reviewer_is_current(item) for item in reviewers)
            ):
                raise AppError(code=IdentityRecoveryErrorCode.CONFLICT, message="Current reviewer authority is insufficient", status_code=409)
            source_user_id, target_user_id = self._case_parties(record)
            rows = self._repository.lock_completion_rows(
                source_user_id=source_user_id,
                target_user_id=target_user_id,
                phone_e164=record.subject_phone_e164,
            )
            users = {item.id: item for item in rows.users}
            source = users.get(source_user_id)
            target = users.get(target_user_id)
            if source is None or target is None or target.status != "ACTIVE":
                raise AppError(code=IdentityRecoveryErrorCode.CONFLICT, message="Identity participants changed", status_code=409)
            if record.purpose != "PHONE_REUSE" and source.status != "ACTIVE":
                raise AppError(code=IdentityRecoveryErrorCode.CONFLICT, message="Source account is not mergeable", status_code=409)
            impact = self._build_impact(record, rows)
            if record.impact_hash != self._impact_hash(impact) or record.impact_json != impact:
                raise AppError(code=IdentityRecoveryErrorCode.CONFLICT, message="Identity impact changed after approval", status_code=409)
            self._reauthenticate_target(
                rows,
                case_id=record.id,
                target_user_id=target_user_id,
                current_password=current_password,
                target_reauth_assertion=target_reauth_assertion,
                new_password=new_password,
                now=now,
            )
            if record.purpose == "PHONE_REUSE":
                if not confirm_replace_phone:
                    self._invalid("Replacing the current phone requires explicit confirmation")
                moved = self._complete_phone_reuse(record, rows, now=now)
                merge_id = None
            else:
                moved, merge_id = self._complete_account_transfer(
                    record,
                    rows,
                    source=source,
                    target=target,
                    now=now,
                )
            record.status = "COMPLETED"
            record.completed_at = now
            record.version += 1
            self._audit.append(
                action=AuditAction.IDENTITY_RECOVERY_COMPLETED,
                resource_type=AuditResourceType.IDENTITY_RECOVERY_CASE,
                actor_user_id=current.user_id,
                resource_id=record.id,
                request_id=request_id,
                scope_type=ScopeType.SYSTEM,
                details={
                    "purpose": record.purpose,
                    "status": record.status,
                    "source_user_id": source_user_id,
                    "target_user_id": target_user_id,
                    "merge_id": merge_id,
                    "impact_hash": record.impact_hash,
                    "moved_counts": moved,
                },
            )
            return record

        try:
            return run_owned_transaction(self._session, operation)
        except AppError:
            raise
        except IntegrityError as exc:
            raise AppError(
                code=IdentityRecoveryErrorCode.CONFLICT,
                message="Identity completion conflicted and was rolled back",
                status_code=409,
            ) from exc

    def list_cases(self, *, actor: Principal, status: str | None, after_id: int, limit: int) -> tuple[IdentityRecoveryCase, ...]:
        self._require_schema()
        self._require_reviewer(actor)
        return self._repository.list_cases(status=status, after_id=after_id, limit=limit)

    def list_my_cases(self, *, actor: Principal, after_id: int, limit: int) -> tuple[IdentityRecoveryCase, ...]:
        self._require_schema()
        current = self._reload_active(actor.user_id)
        return self._repository.list_requester_cases(user_id=current.user_id, after_id=after_id, limit=limit)

    def approval_count(self, case_id: int) -> int:
        return len({item.reviewer_user_id for item in self._repository.list_reviews(case_id) if item.decision == "APPROVE"})

    def _schema_available(self) -> bool:
        try:
            inspector = inspect(self._session.get_bind())
            return inspector.has_table("identity_recovery_cases") is True and inspector.has_table("identity_recovery_reviews") is True
        except (AttributeError, SQLAlchemyError):
            return False

    def _require_schema(self) -> None:
        if not self._schema_available():
            raise AppError(code=IdentityRecoveryErrorCode.UNAVAILABLE, message="Identity recovery awaits approved schema migration", status_code=503)

    def _require_enabled(self) -> None:
        self._require_schema()
        if self._provider.available is not True:
            raise AppError(code=IdentityRecoveryErrorCode.UNAVAILABLE, message="No approved identity proof channel is configured", status_code=503)

    @staticmethod
    def _require_reviewer(actor: Principal) -> None:
        try:
            RbacService.require_permission(actor, IDENTITY_REVIEW, OrganizationScope(ScopeType.SYSTEM))
        except AppError as exc:
            raise AppError(code=IamErrorCode.FORBIDDEN, message="Permission denied", status_code=403) from exc

    def _reload_active(self, user_id: int) -> Principal:
        if self._principal_loader is None:
            raise AppError(code=IdentityRecoveryErrorCode.UNAVAILABLE, message="Identity principal loader is unavailable", status_code=503)
        principal = self._principal_loader(user_id)
        if principal.status != "ACTIVE":
            raise AppError(code=IamErrorCode.FORBIDDEN, message="Permission denied", status_code=403)
        return principal

    def _reload_reviewer(self, user_id: int) -> Principal:
        principal = self._reload_active(user_id)
        self._require_reviewer(principal)
        return principal

    def _reviewer_is_current(self, user_id: int) -> bool:
        try:
            self._reload_reviewer(user_id)
            return True
        except AppError:
            return False

    @staticmethod
    def _case_parties(record: IdentityRecoveryCase) -> tuple[int, int]:
        if record.source_user_id is None or record.target_user_id is None:
            raise AppError(
                code=IdentityRecoveryErrorCode.CONFLICT,
                message="Recovery case does not bind both identity participants",
                status_code=409,
            )
        if record.source_user_id == record.target_user_id:
            raise AppError(code=IdentityRecoveryErrorCode.CONFLICT, message="Identity participants conflict", status_code=409)
        return record.source_user_id, record.target_user_id

    @staticmethod
    def _impact_hash(impact: dict[str, object]) -> str:
        payload = json.dumps(impact, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return sha256(payload.encode("utf-8")).hexdigest()

    def _build_impact(self, record: IdentityRecoveryCase, rows: IdentityCompletionRows) -> dict[str, object]:
        source_id, target_id = self._case_parties(record)
        pending_requests = sorted(
            item.id for item in rows.pending_bind_requests
            if item.status == "PENDING"
        )
        if pending_requests:
            raise AppError(code=IdentityRecoveryErrorCode.CONFLICT, message="Pending resident requests must be resolved first", status_code=409)
        pending_deliveries = sorted(
            item.id for item in rows.pending_deliveries if item.status == "PENDING"
        )
        if record.purpose != "PHONE_REUSE" and pending_deliveries:
            raise AppError(code=IdentityRecoveryErrorCode.CONFLICT, message="Pending source notifications must settle first", status_code=409)
        source_employees = [item for item in rows.employees if item.user_id == source_id]
        target_employees = [item for item in rows.employees if item.user_id == target_id]
        if record.purpose != "PHONE_REUSE" and source_employees and target_employees:
            raise AppError(code=IdentityRecoveryErrorCode.CONFLICT, message="Both accounts own employee records", status_code=409)
        source_wechat_apps = {item.appid for item in rows.wechats if item.user_id == source_id}
        target_wechat_apps = {item.appid for item in rows.wechats if item.user_id == target_id}
        if record.purpose != "PHONE_REUSE" and source_wechat_apps & target_wechat_apps:
            raise AppError(code=IdentityRecoveryErrorCode.CONFLICT, message="WeChat bindings conflict", status_code=409)
        if record.purpose != "PHONE_REUSE":
            target_active_relations = {
                item.house_id: item
                for item in rows.relations
                if item.user_id == target_id and item.status == "ACTIVE"
            }
            for source_relation in rows.relations:
                if source_relation.user_id != source_id or source_relation.status != "ACTIVE":
                    continue
                target_relation = target_active_relations.get(source_relation.house_id)
                if target_relation is not None and target_relation.relation_type != source_relation.relation_type:
                    raise AppError(
                        code=IdentityRecoveryErrorCode.CONFLICT,
                        message="Active house relationships conflict",
                        status_code=409,
                    )
        source_active = [
            item for item in rows.phones
            if item.user_id == source_id and item.status == "ACTIVE"
        ]
        subject_rows = [item for item in rows.phones if item.phone_e164 == record.subject_phone_e164]
        if not subject_rows or subject_rows[-1].user_id != source_id:
            raise AppError(code=IdentityRecoveryErrorCode.CONFLICT, message="Phone ownership history changed", status_code=409)
        if record.purpose == "PHONE_REUSE":
            if any(item.status == "ACTIVE" for item in subject_rows):
                raise AppError(code=IdentityRecoveryErrorCode.CONFLICT, message="Phone is still active", status_code=409)
            active_tombstones = [item for item in rows.tombstones if item.status in {"QUARANTINED", "RELEASED"}]
            if any(item.source_user_id != source_id for item in active_tombstones):
                raise AppError(code=IdentityRecoveryErrorCode.CONFLICT, message="Phone quarantine ownership changed", status_code=409)
        if record.purpose == "MERGE_SOURCE_PROOF" and (
            len(source_active) != 1 or source_active[0].phone_e164 != record.subject_phone_e164
        ):
            raise AppError(code=IdentityRecoveryErrorCode.CONFLICT, message="Source current phone changed", status_code=409)
        if record.purpose == "ACCOUNT_RECOVERY" and source_active:
            raise AppError(
                code=IdentityRecoveryErrorCode.CONFLICT,
                message="Account recovery requires a source account without an active phone",
                status_code=409,
            )
        return {
            "purpose": record.purpose,
            "source_user_id": source_id,
            "target_user_id": target_id,
            "subject_phone_row_id": subject_rows[-1].id,
            "subject_phone_hash": self._hash(subject_rows[-1].phone_e164),
            "source_employees": sorted(
                (
                    {
                        "id": item.id,
                        "tenant_id": getattr(item, "tenant_id", None),
                    }
                    for item in source_employees
                ),
                key=lambda item: item["id"],
            ),
            "source_file_ids": sorted(item.id for item in rows.files if item.uploader_user_id == source_id),
            "source_notification_ids": sorted(item.id for item in rows.notifications if item.user_id == source_id),
            "source_relations": sorted(
                (
                    {
                        "id": item.id,
                        "tenant_id": getattr(item, "tenant_id", None),
                        "property_id": getattr(item, "property_id", None),
                        "house_id": item.house_id,
                        "relation_type": item.relation_type,
                        "status": item.status,
                    }
                    for item in rows.relations
                    if item.user_id == source_id
                ),
                key=lambda item: item["id"],
            ),
            "source_roles": sorted(
                (
                    {
                        "id": item.id,
                        "role_id": item.role_id,
                        "scope_type": item.scope_type,
                        "tenant_id": item.tenant_id,
                        "property_id": item.property_id,
                    }
                    for item in rows.roles
                    if item.user_id == source_id
                ),
                key=lambda item: item["id"],
            ),
            "target_relations": sorted(
                (
                    {
                        "id": item.id,
                        "tenant_id": getattr(item, "tenant_id", None),
                        "property_id": getattr(item, "property_id", None),
                        "house_id": item.house_id,
                        "relation_type": item.relation_type,
                        "status": item.status,
                    }
                    for item in rows.relations
                    if item.user_id == target_id
                ),
                key=lambda item: item["id"],
            ),
            "target_roles": sorted(
                (
                    {
                        "id": item.id,
                        "role_id": item.role_id,
                        "scope_type": item.scope_type,
                        "tenant_id": item.tenant_id,
                        "property_id": item.property_id,
                    }
                    for item in rows.roles
                    if item.user_id == target_id
                ),
                key=lambda item: item["id"],
            ),
            "source_session_ids": sorted(item.id for item in rows.sessions if item.user_id == source_id and item.revoked_at is None),
            "target_session_ids": sorted(item.id for item in rows.sessions if item.user_id == target_id and item.revoked_at is None),
            "source_ticket_ids": sorted(item.id for item in rows.reset_tickets if item.user_id == source_id and item.consumed_at is None),
            "target_ticket_ids": sorted(item.id for item in rows.reset_tickets if item.user_id == target_id and item.consumed_at is None),
            "source_password_ids": sorted(item.id for item in rows.passwords if item.user_id == source_id),
            "target_password_ids": sorted(item.id for item in rows.passwords if item.user_id == target_id),
            "source_wechat": sorted(
                (
                    {
                        "id": item.id,
                        "appid": item.appid,
                        "identity_hash": self._hash(
                            "|".join(
                                (
                                    item.appid,
                                    str(getattr(item, "openid", "")),
                                    str(getattr(item, "unionid", "")),
                                )
                            )
                        ),
                    }
                    for item in rows.wechats
                    if item.user_id == source_id
                ),
                key=lambda item: item["id"],
            ),
            "target_wechat": sorted(
                (
                    {
                        "id": item.id,
                        "appid": item.appid,
                        "identity_hash": self._hash(
                            "|".join(
                                (
                                    item.appid,
                                    str(getattr(item, "openid", "")),
                                    str(getattr(item, "unionid", "")),
                                )
                            )
                        ),
                    }
                    for item in rows.wechats
                    if item.user_id == target_id
                ),
                key=lambda item: item["id"],
            ),
            "source_work_order_ids": sorted(item.id for item in rows.work_orders if item.reporter_user_id == source_id),
            "target_active_phones": sorted(
                (
                    {"id": item.id, "phone_hash": self._hash(item.phone_e164)}
                    for item in rows.phones
                    if item.user_id == target_id and item.status == "ACTIVE"
                ),
                key=lambda item: item["id"],
            ),
            "existing_merges": sorted(
                ({"id": item.id, "status": item.status} for item in rows.merges),
                key=lambda item: item["id"],
            ),
            "tombstones": sorted(
                (
                    {
                        "id": item.id,
                        "source_user_id": item.source_user_id,
                        "status": item.status,
                    }
                    for item in rows.tombstones
                ),
                key=lambda item: item["id"],
            ),
        }

    def _reauthenticate_target(
        self,
        rows: IdentityCompletionRows,
        *,
        case_id: int,
        target_user_id: int,
        current_password: str | None,
        target_reauth_assertion: str | None,
        new_password: str | None,
        now: datetime,
    ) -> None:
        target_password = next((item for item in rows.passwords if item.user_id == target_user_id), None)
        if target_password is not None:
            if current_password is None or not verify_password(target_password.password_hash, current_password):
                raise AppError(code=IdentityRecoveryErrorCode.PROOF_REJECTED, message="Target reauthentication failed", status_code=401)
        else:
            if new_password is None:
                self._invalid("A new target password is required")
            target_phones = [
                item
                for item in rows.phones
                if item.user_id == target_user_id and item.status == "ACTIVE"
            ]
            if len(target_phones) != 1 or not target_reauth_assertion:
                raise AppError(
                    code=IdentityRecoveryErrorCode.PROOF_REJECTED,
                    message="Recent target identity proof is required",
                    status_code=401,
                )
            try:
                proof = self._provider.verify(
                    assertion=target_reauth_assertion,
                    purpose=f"TARGET_REAUTH:{case_id}",
                    phone_e164=target_phones[0].phone_e164,
                )
            except Exception as exc:
                raise AppError(
                    code=IdentityRecoveryErrorCode.PROOF_REJECTED,
                    message="Target reauthentication failed",
                    status_code=401,
                ) from exc
            if not proof.verified:
                raise AppError(
                    code=IdentityRecoveryErrorCode.PROOF_REJECTED,
                    message="Target reauthentication failed",
                    status_code=401,
                )
            require_new_password(new_password)
            self._repository.add_password(
                UserPassword(
                    user_id=target_user_id,
                    password_hash=hash_password(new_password),
                    password_algo="argon2id",
                    created_at=now,
                    updated_at=now,
                )
            )

    def _revoke_target_credentials(self, rows: IdentityCompletionRows, *, user_ids: set[int], now: datetime) -> None:
        for item in rows.sessions:
            if item.user_id in user_ids and item.revoked_at is None:
                item.revoked_at = now
        for item in rows.reset_tickets:
            if item.user_id in user_ids and item.consumed_at is None:
                item.consumed_at = now

    def _complete_phone_reuse(
        self, record: IdentityRecoveryCase, rows: IdentityCompletionRows, *, now: datetime
    ) -> dict[str, int]:
        source_id, target_id = self._case_parties(record)
        if any(item.phone_e164 == record.subject_phone_e164 and item.status == "ACTIVE" for item in rows.phones):
            raise AppError(code=IdentityRecoveryErrorCode.CONFLICT, message="Phone is already active", status_code=409)
        for item in rows.phones:
            if item.user_id == target_id and item.status == "ACTIVE":
                item.status = "ENDED"
                item.ended_at = now
        self._repository.add_phone(
            UserPhone(
                user_id=target_id,
                phone_e164=record.subject_phone_e164,
                status="ACTIVE",
                verified_at=now,
                ended_at=None,
            )
        )
        tombstone = next(
            (item for item in reversed(rows.tombstones) if item.status in {"QUARANTINED", "RELEASED"}),
            None,
        )
        if tombstone is None:
            tombstone = self._repository.add_tombstone(
                PhoneIdentityTombstone(
                    phone_e164=record.subject_phone_e164,
                    source_user_id=source_id,
                    recovery_case_id=record.id,
                    status="CLAIMED",
                    quarantine_until=record.quarantine_ends_at,
                    released_at=record.quarantine_ends_at,
                    claimed_at=now,
                )
            )
        else:
            tombstone.status = "CLAIMED"
            tombstone.claimed_at = now
            tombstone.recovery_case_id = record.id
        self._revoke_target_credentials(rows, user_ids={target_id}, now=now)
        return {"phones_created": 1, "accounts_merged": 0}

    def _complete_account_transfer(
        self,
        record: IdentityRecoveryCase,
        rows: IdentityCompletionRows,
        *,
        source,
        target,
        now: datetime,
    ) -> tuple[dict[str, int], int]:
        source_id, target_id = source.id, target.id
        if any(item.status == "COMPLETED" and item.source_user_id == source_id for item in rows.merges):
            raise AppError(code=IdentityRecoveryErrorCode.CONFLICT, message="Source account was already merged", status_code=409)
        target_role_keys = {
            (item.role_id, item.scope_type, item.tenant_id, item.property_id)
            for item in rows.roles if item.user_id == target_id
        }
        moved_roles = 0
        deduplicated_roles = 0
        for item in rows.roles:
            if item.user_id != source_id:
                continue
            key = (item.role_id, item.scope_type, item.tenant_id, item.property_id)
            if key in target_role_keys:
                self._repository.delete_role(item)
                deduplicated_roles += 1
                continue
            item.user_id = target_id
            target_role_keys.add(key)
            moved_roles += 1
        target_active_relations = {
            item.house_id: item
            for item in rows.relations
            if item.user_id == target_id and item.status == "ACTIVE"
        }
        moved_relations = 0
        for item in rows.relations:
            if item.user_id != source_id:
                continue
            duplicate = target_active_relations.get(item.house_id) if item.status == "ACTIVE" else None
            if duplicate is not None:
                if duplicate.relation_type != item.relation_type:
                    raise AppError(code=IdentityRecoveryErrorCode.CONFLICT, message="Active house relationships conflict", status_code=409)
                item.status = "ENDED"
                item.valid_to = now
                continue
            item.user_id = target_id
            moved_relations += 1
        moved_employees = 0
        for item in rows.employees:
            if item.user_id == source_id:
                item.user_id = target_id
                moved_employees += 1
        moved_wechats = 0
        for item in rows.wechats:
            if item.user_id == source_id:
                item.user_id = target_id
                moved_wechats += 1
        moved_files = 0
        for item in rows.files:
            if item.uploader_user_id == source_id:
                item.uploader_user_id = target_id
                moved_files += 1
        moved_notifications = 0
        for item in rows.notifications:
            if item.user_id == source_id:
                item.user_id = target_id
                moved_notifications += 1
        moved_work_orders = 0
        for item in rows.work_orders:
            if item.reporter_user_id == source_id:
                item.reporter_user_id = target_id
                moved_work_orders += 1
        source_phone = next(
            item for item in reversed(rows.phones)
            if item.user_id == source_id and item.phone_e164 == record.subject_phone_e164
        )
        if source_phone.status == "ACTIVE":
            source_phone.status = "ENDED"
            source_phone.ended_at = now
        source_password = next((item for item in rows.passwords if item.user_id == source_id), None)
        if source_password is not None:
            self._repository.delete_password(source_password)
        self._revoke_target_credentials(rows, user_ids={source_id, target_id}, now=now)
        tombstone = self._repository.add_tombstone(
            PhoneIdentityTombstone(
                phone_e164=record.subject_phone_e164,
                source_user_id=source_id,
                recovery_case_id=record.id,
                status="RELEASED",
                quarantine_until=record.quarantine_ends_at,
                released_at=now,
                claimed_at=None,
            )
        )
        del tombstone
        source.status = "MERGED"
        merge = self._repository.add_merge(
            AccountMerge(
                source_user_id=source_id,
                target_user_id=target_id,
                initiated_by_user_id=target_id,
                source_phone_id=source_phone.id,
                recovery_case_id=record.id,
                status="COMPLETED",
                impact_json=record.impact_json,
                impact_hash=record.impact_hash,
                target_reauthenticated_at=now,
                source_verified_at=record.proof_verified_at,
                completed_at=now,
            )
        )
        return (
            {
                "employees": moved_employees,
                "files": moved_files,
                "notifications": moved_notifications,
                "relations": moved_relations,
                "roles": moved_roles,
                "roles_deduplicated": deduplicated_roles,
                "wechats": moved_wechats,
                "work_orders": moved_work_orders,
            },
            merge.id,
        )

    @staticmethod
    def _hash(value: str) -> str:
        return sha256(value.encode("utf-8")).hexdigest()

    @staticmethod
    def _invalid(message: str):
        raise AppError(code=IdentityRecoveryErrorCode.INVALID_INPUT, message=message, status_code=422)
