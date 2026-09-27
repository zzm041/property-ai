"""Guarded high-risk identity proof and two-reviewer workflow.

The service is inert until both the pending schema and an approved proof provider
exist. SMS possession alone is deliberately not a proof provider.
"""

from collections.abc import Callable
from datetime import datetime, timedelta
from hashlib import sha256

from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.database import run_owned_transaction
from app.core.errors import AppError, IdentityRecoveryErrorCode, IamErrorCode
from app.core.time import utc_now
from app.domain.audit import AuditAction, AuditResourceType
from app.domain.iam import OrganizationScope, Principal, ScopeType
from app.domain.phone import normalize_phone
from app.model.identity import IdentityRecoveryCase, IdentityRecoveryReview
from app.provider.identity_proof import IdentityProofProvider, UnavailableIdentityProofProvider
from app.repository.identity_recovery import IdentityRecoveryRepository
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
            if purpose == "MERGE_SOURCE_PROOF" and resolved_source is None:
                self._invalid("Merge source account could not be established")
            if purpose == "MERGE_SOURCE_PROOF" and resolved_source == current.user_id:
                self._invalid("Merge source and target must be different users")
            record = self._repository.add_case(
                IdentityRecoveryCase(
                    purpose=purpose,
                    requester_user_id=current.user_id,
                    source_user_id=resolved_source,
                    target_user_id=current.user_id if purpose == "MERGE_SOURCE_PROOF" else None,
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
            if len(reviewers) < 2 or not all(self._reviewer_is_current(user_id) for user_id in reviewers):
                raise AppError(code=IdentityRecoveryErrorCode.CONFLICT, message="Two distinct currently authorized approvals are required", status_code=409)
            record.status = "APPROVED"
            record.review_completed_at = now
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

    def list_cases(self, *, actor: Principal, status: str | None, after_id: int, limit: int) -> tuple[IdentityRecoveryCase, ...]:
        self._require_schema()
        self._require_reviewer(actor)
        return self._repository.list_cases(status=status, after_id=after_id, limit=limit)

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
    def _hash(value: str) -> str:
        return sha256(value.encode("utf-8")).hexdigest()

    @staticmethod
    def _invalid(message: str):
        raise AppError(code=IdentityRecoveryErrorCode.INVALID_INPUT, message=message, status_code=422)
