from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy.exc import SQLAlchemyError

from app.core.errors import AppError, IdentityRecoveryErrorCode
from app.domain.iam import OrganizationScope, Principal, RoleGrant, ScopeType
from app.model.identity import IdentityRecoveryCase
from app.provider.identity_proof import IdentityProofResult
from app.service.identity_recovery import IdentityRecoveryService
from app.service.credential_auth import CredentialAuthService
from app.service import credential_auth as credential_auth_module


NOW = datetime(2026, 9, 25, 6, 0, tzinfo=timezone.utc)


class FakeProof:
    available = True

    def verify(self, *, assertion: str, purpose: str, phone_e164: str) -> IdentityProofResult:
        assert assertion == "opaque-proof-assertion"
        return IdentityProofResult(
            provider="OFFLINE_TEST_ONLY",
            reference="provider-reference-secret",
            subject_reference="subject-reference-secret",
            verified=True,
        )


def principal(user_id: int, *, reviewer: bool = False) -> Principal:
    grants = ()
    if reviewer:
        grants = (
            RoleGrant(
                role_code="SYSTEM_ADMIN",
                scope=OrganizationScope(ScopeType.SYSTEM),
                permissions=frozenset({"identity:review"}),
            ),
        )
    return Principal(user_id=user_id, status="ACTIVE", display_name=f"u{user_id}", grants=grants, auth_method="password")


def service(*, provider=None, loader=None) -> IdentityRecoveryService:
    session = MagicMock()
    session.info = {}
    session.in_transaction.return_value = False
    item = IdentityRecoveryService.__new__(IdentityRecoveryService)
    item._session = session
    item._provider = provider or SimpleNamespace(available=False)
    item._repository = MagicMock()
    item._audit = MagicMock()
    item._principal_loader = loader or (lambda user_id: principal(user_id))
    item._clock = lambda: NOW
    item._schema_available = MagicMock(return_value=True)
    return item


def test_missing_proof_provider_disables_case_without_writing() -> None:
    item = service()
    with pytest.raises(AppError) as captured:
        item.create_case(
            actor=principal(10),
            purpose="MERGE_SOURCE_PROOF",
            phone="13800000123",
            proof_assertion="opaque-proof-assertion",
            source_user_id=20,
            request_id="recovery-unit",
        )
    assert captured.value.code == IdentityRecoveryErrorCode.UNAVAILABLE
    item._repository.add_case.assert_not_called()


def test_verified_case_persists_only_hashes_and_thirty_day_quarantine() -> None:
    item = service(provider=FakeProof())
    item._repository.find_active_case.return_value = None
    item._repository.latest_phone_owner.return_value = SimpleNamespace(user_id=20)

    def add_case(record):
        record.id = 91
        return record

    item._repository.add_case.side_effect = add_case
    created = item.create_case(
        actor=principal(10),
        purpose="MERGE_SOURCE_PROOF",
        phone="13800000123",
        proof_assertion="opaque-proof-assertion",
        source_user_id=20,
        request_id="recovery-unit",
    )
    assert created.status == "PENDING_REVIEW"
    assert created.quarantine_ends_at == NOW + timedelta(days=30)
    assert created.proof_reference_hash != "provider-reference-secret"
    assert created.proof_result_hash != "subject-reference-secret"
    assert "opaque-proof-assertion" not in repr(created.__dict__)


def test_caller_cannot_override_recorded_phone_owner() -> None:
    item = service(provider=FakeProof())
    item._repository.find_active_case.return_value = None
    item._repository.latest_phone_owner.return_value = SimpleNamespace(user_id=21)

    with pytest.raises(AppError) as captured:
        item.create_case(
            actor=principal(10),
            purpose="MERGE_SOURCE_PROOF",
            phone="13800000123",
            proof_assertion="opaque-proof-assertion",
            source_user_id=20,
            request_id="recovery-owner-mismatch",
        )

    assert captured.value.code == IdentityRecoveryErrorCode.INVALID_INPUT
    item._repository.add_case.assert_not_called()


def test_finalize_requires_two_current_distinct_reviewers_and_elapsed_quarantine() -> None:
    people = {1: principal(1, reviewer=True), 2: principal(2, reviewer=True), 3: principal(3, reviewer=True)}
    item = service(provider=FakeProof(), loader=people.__getitem__)
    record = SimpleNamespace(
        id=91,
        version=3,
        status="PENDING_REVIEW",
        purpose="MERGE_SOURCE_PROOF",
        quarantine_ends_at=NOW - timedelta(seconds=1),
        expires_at=NOW + timedelta(days=1),
        review_completed_at=None,
    )
    item._repository.get_case_for_update.return_value = record
    item._repository.list_reviews.return_value = (
        SimpleNamespace(reviewer_user_id=1, decision="APPROVE"),
        SimpleNamespace(reviewer_user_id=2, decision="APPROVE"),
    )

    result = item.finalize_case(
        actor=people[3],
        case_id=91,
        expected_version=3,
        request_id="finalize-unit",
    )
    assert result.status == "APPROVED"
    assert result.version == 4
    assert item._audit.append.call_args.kwargs["details"]["reviewer_count"] == 2


def test_finalize_does_not_treat_elapsed_time_as_approval() -> None:
    reviewer = principal(1, reviewer=True)
    item = service(provider=FakeProof(), loader=lambda _user_id: reviewer)
    item._repository.get_case_for_update.return_value = SimpleNamespace(
        id=91,
        version=1,
        status="PENDING_REVIEW",
        purpose="PHONE_REUSE",
        quarantine_ends_at=NOW - timedelta(days=1),
        expires_at=NOW + timedelta(days=1),
    )
    item._repository.list_reviews.return_value = ()
    with pytest.raises(AppError) as captured:
        item.finalize_case(actor=reviewer, case_id=91, expected_version=1, request_id="no-review")
    assert captured.value.code == IdentityRecoveryErrorCode.CONFLICT


def test_generic_sms_cannot_recreate_an_ended_identity_after_recovery_schema() -> None:
    item = CredentialAuthService.__new__(CredentialAuthService)
    item._repository = MagicMock()
    item._repository.get_latest_phone_for_update.return_value = SimpleNamespace(status="ENDED")
    item._identity_recovery_available = MagicMock(return_value=True)

    with pytest.raises(AppError) as captured:
        item._require_phone_reuse_clear("+8613800000123")

    assert captured.value.code == "AUTH.IDENTITY_RECOVERY_REQUIRED"


def test_legacy_sms_behavior_is_unchanged_without_recovery_schema() -> None:
    item = CredentialAuthService.__new__(CredentialAuthService)
    item._repository = MagicMock()
    item._identity_recovery_available = MagicMock(return_value=False)

    item._require_phone_reuse_clear("+8613800000123")

    item._repository.get_latest_phone_for_update.assert_not_called()


def test_recovery_schema_inspection_failure_does_not_fail_open(monkeypatch) -> None:
    item = CredentialAuthService.__new__(CredentialAuthService)
    item._session = MagicMock()
    item._repository = MagicMock()
    inspector = MagicMock()
    inspector.has_table.side_effect = SQLAlchemyError("metadata temporarily unavailable")
    inspect_mock = MagicMock(return_value=inspector)
    monkeypatch.setattr(credential_auth_module, "inspect", inspect_mock)

    with pytest.raises(SQLAlchemyError):
        item._require_phone_reuse_clear("+8613800000123")

    inspect_mock.assert_called_once_with(item._session.get_bind.return_value)
    inspector.has_table.assert_called_once_with("identity_recovery_cases")
    item._repository.get_latest_phone_for_update.assert_not_called()
    item._repository.add_user.assert_not_called()
