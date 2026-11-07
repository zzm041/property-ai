from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.dialects import mysql

from pydantic import ValidationError

from app.core.config import Settings
from app.core.errors import AppError, IdentityRecoveryErrorCode
from app.domain.iam import OrganizationScope, Principal, RoleGrant, ScopeType
from app.model.identity import IdentityRecoveryCase
from app.provider.identity_proof import IdentityProofResult, IsolatedTestIdentityProofProvider
from app.repository.identity_recovery import IdentityCompletionRows
from app.schema.identity_recovery import CompleteIdentityRecoveryCaseRequest
from app.service.identity_recovery import IdentityRecoveryService
from app.service.identity_write_guard import lock_active_identity
from app.service.credential_auth import CredentialAuthService
from app.service import credential_auth as credential_auth_module
from app.service import identity_recovery as identity_recovery_module


NOW = datetime(2026, 9, 25, 6, 0, tzinfo=timezone.utc)
REPO_ROOT = Path(__file__).resolve().parents[3]


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


def row(**values):
    return SimpleNamespace(**values)


def completion_rows(*, target_has_password: bool = True, purpose: str = "MERGE_SOURCE_PROOF") -> IdentityCompletionRows:
    source_phone_status = "ENDED" if purpose in {"PHONE_REUSE", "ACCOUNT_RECOVERY"} else "ACTIVE"
    source_phone = row(
        id=11,
        user_id=20,
        phone_e164="+8613800000123",
        status=source_phone_status,
        ended_at=NOW - timedelta(days=31) if source_phone_status == "ENDED" else None,
    )
    target_phone = row(
        id=12,
        user_id=10,
        phone_e164="+8613900000123",
        status="ACTIVE",
        ended_at=None,
    )
    passwords = [row(id=21, user_id=20, password_hash="source-hash")]
    if target_has_password:
        passwords.append(row(id=22, user_id=10, password_hash="target-hash"))
    return IdentityCompletionRows(
        users=(row(id=10, status="ACTIVE"), row(id=20, status="ACTIVE")),
        phones=(source_phone, target_phone),
        passwords=tuple(passwords),
        sessions=(
            row(id=31, user_id=20, revoked_at=None),
            row(id=32, user_id=10, revoked_at=None),
        ),
        reset_tickets=(
            row(id=41, user_id=20, consumed_at=None),
            row(id=42, user_id=10, consumed_at=None),
        ),
        wechats=(row(id=51, user_id=20, appid="source-app", unionid="source-union"),),
        roles=(
            row(id=61, user_id=20, role_id=3, scope_type="PROPERTY", tenant_id=1, property_id=1),
        ),
        employees=(row(id=71, user_id=20),),
        relations=(
            row(id=81, user_id=20, house_id=1, status="ACTIVE", relation_type="OWNER", valid_to=None),
        ),
        pending_bind_requests=(),
        files=(row(id=91, uploader_user_id=20),),
        notifications=(row(id=101, user_id=20),),
        pending_deliveries=(row(id=111, status="SUCCEEDED"),),
        work_orders=(row(id=121, reporter_user_id=20),),
        tombstones=(),
        merges=(),
    )


def approved_case(item: IdentityRecoveryService, rows: IdentityCompletionRows, *, purpose: str = "MERGE_SOURCE_PROOF"):
    record = row(
        id=91,
        version=4,
        status="APPROVED",
        purpose=purpose,
        requester_user_id=10,
        source_user_id=20,
        target_user_id=10,
        subject_phone_e164="+8613800000123",
        quarantine_ends_at=NOW - timedelta(seconds=1),
        expires_at=NOW + timedelta(days=1),
        proof_verified_at=NOW - timedelta(days=30),
        review_completed_at=NOW - timedelta(seconds=1),
        completed_at=None,
        impact_json=None,
        impact_hash=None,
    )
    record.impact_json = item._build_impact(record, rows)
    record.impact_hash = item._impact_hash(record.impact_json)
    return record


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
        requester_user_id=10,
        source_user_id=20,
        target_user_id=10,
        subject_phone_e164="+8613800000123",
        quarantine_ends_at=NOW - timedelta(seconds=1),
        expires_at=NOW + timedelta(days=1),
        review_completed_at=None,
        impact_json=None,
        impact_hash=None,
    )
    rows = completion_rows()
    item._repository.lock_completion_rows.return_value = rows
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
    assert result.impact_hash == item._impact_hash(result.impact_json)
    item._repository.add_merge.assert_not_called()
    assert item._audit.append.call_args.kwargs["details"]["reviewer_count"] == 2


def test_finalize_does_not_treat_elapsed_time_as_approval() -> None:
    reviewer = principal(1, reviewer=True)
    item = service(provider=FakeProof(), loader=lambda _user_id: reviewer)
    item._repository.get_case_for_update.return_value = SimpleNamespace(
        id=91,
        version=1,
        status="PENDING_REVIEW",
        purpose="PHONE_REUSE",
        requester_user_id=10,
        source_user_id=20,
        target_user_id=10,
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


def test_isolated_test_proof_is_signed_bound_and_expires() -> None:
    clock = [NOW]
    provider = IsolatedTestIdentityProofProvider(
        secret="local-test-only-secret",
        ttl_seconds=30,
        clock=lambda: clock[0],
    )
    token = provider.issue(
        purpose="PHONE_REUSE",
        phone_e164="+8613800000123",
        subject_reference="synthetic-person-1",
    )
    accepted = provider.verify(
        assertion=token,
        purpose="PHONE_REUSE",
        phone_e164="+8613800000123",
    )
    assert accepted.verified is True
    assert "synthetic-person-1" not in accepted.reference
    assert provider.verify(
        assertion=token,
        purpose="ACCOUNT_RECOVERY",
        phone_e164="+8613800000123",
    ).verified is False
    clock[0] = NOW + timedelta(seconds=31)
    assert provider.verify(
        assertion=token,
        purpose="PHONE_REUSE",
        phone_e164="+8613800000123",
    ).verified is False


def test_test_proof_switch_requires_exact_auth001_target() -> None:
    settings = Settings(
        _env_file=None,
        app_env="test",
        test_database_url="mysql+pymysql://u:p@127.0.0.1:3307/property_ai_auth001_test",
        identity_test_proof_enabled=True,
        identity_test_proof_secret="private-test-secret",
        identity_test_proof_target="127.0.0.1:3307/property_ai_auth001_test",
    )
    assert settings.identity_test_proof_enabled is True
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            app_env="test",
            test_database_url="mysql+pymysql://u:p@127.0.0.1:3307/property_ai_test",
            identity_test_proof_enabled=True,
            identity_test_proof_secret="private-test-secret",
            identity_test_proof_target="127.0.0.1:3307/property_ai_test",
        )


def test_test_proof_switch_accepts_only_explicit_retry_3317_target() -> None:
    settings = Settings(
        _env_file=None,
        app_env="test",
        database_url="mysql+pymysql://u:p@127.0.0.1:3306/property_ai",
        test_database_url="mysql+pymysql://u:p@127.0.0.1:3317/property_ai_auth001_test",
        identity_test_proof_enabled=True,
        identity_test_proof_secret="private-test-secret",
        identity_test_proof_target="127.0.0.1:3317/property_ai_auth001_test",
    )
    assert settings.identity_test_proof_enabled is True
    with pytest.raises(ValueError, match="exact AUTH001"):
        Settings(
            _env_file=None,
            app_env="test",
            database_url="mysql+pymysql://u:p@127.0.0.1:3306/property_ai",
            test_database_url="mysql+pymysql://u:p@127.0.0.1:3317/property_ai_auth001_test",
            identity_test_proof_enabled=True,
            identity_test_proof_secret="private-test-secret",
            identity_test_proof_target="127.0.0.1:3307/property_ai_auth001_test",
        )


def test_complete_request_requires_matching_confirmation() -> None:
    with pytest.raises(ValidationError):
        CompleteIdentityRecoveryCaseRequest(
            expected_version=4,
            new_password="lowercase1",
            confirm_password="different1",
        )


def test_account_merge_completion_moves_current_ownership_and_revokes_credentials(monkeypatch) -> None:
    people = {1: principal(1, reviewer=True), 2: principal(2, reviewer=True), 10: principal(10)}
    item = service(loader=people.__getitem__)
    rows = completion_rows()
    record = approved_case(item, rows)
    item._repository.get_case_for_update.return_value = record
    item._repository.lock_completion_rows.return_value = rows
    item._repository.list_reviews.return_value = (
        row(reviewer_user_id=1, decision="APPROVE"),
        row(reviewer_user_id=2, decision="APPROVE"),
    )
    item._repository.add_merge.side_effect = lambda merge: (setattr(merge, "id", 501) or merge)
    item._repository.add_tombstone.side_effect = lambda value: value
    monkeypatch.setattr(identity_recovery_module, "verify_password", lambda hashed, supplied: hashed == "target-hash" and supplied == "target-password1")

    result = item.complete_case(
        actor=people[10],
        case_id=record.id,
        expected_version=4,
        current_password="target-password1",
        target_reauth_assertion=None,
        new_password=None,
        confirm_replace_phone=False,
        request_id="complete-unit",
    )

    assert result.status == "COMPLETED"
    assert result.version == 5
    assert rows.users[1].status == "MERGED"
    assert rows.phones[0].status == "ENDED"
    assert all(session.revoked_at == NOW for session in rows.sessions)
    assert all(ticket.consumed_at == NOW for ticket in rows.reset_tickets)
    assert rows.employees[0].user_id == 10
    assert rows.files[0].uploader_user_id == 10
    assert rows.notifications[0].user_id == 10
    assert rows.work_orders[0].reporter_user_id == 10
    item._repository.delete_password.assert_called_once_with(rows.passwords[0])
    assert item._repository.add_merge.call_args.args[0].impact_hash == record.impact_hash
    assert item._audit.append.call_args.kwargs["details"]["merge_id"] == 501


def test_completed_confirmation_is_idempotent_without_reapplying() -> None:
    item = service(loader=lambda _user_id: principal(10))
    record = row(id=91, requester_user_id=10, target_user_id=10, status="COMPLETED")
    item._repository.get_case_for_update.return_value = record
    result = item.complete_case(
        actor=principal(10),
        case_id=91,
        expected_version=4,
        current_password="irrelevant",
        target_reauth_assertion=None,
        new_password=None,
        confirm_replace_phone=False,
        request_id="repeat-unit",
    )
    assert result is record
    item._repository.lock_completion_rows.assert_not_called()
    item._repository.add_merge.assert_not_called()
    item._audit.append.assert_not_called()


def test_changed_impact_blocks_completion_before_any_mutation(monkeypatch) -> None:
    people = {1: principal(1, reviewer=True), 2: principal(2, reviewer=True), 10: principal(10)}
    item = service(loader=people.__getitem__)
    rows = completion_rows()
    record = approved_case(item, rows)
    record.impact_hash = "0" * 64
    item._repository.get_case_for_update.return_value = record
    item._repository.lock_completion_rows.return_value = rows
    item._repository.list_reviews.return_value = (
        row(reviewer_user_id=1, decision="APPROVE"),
        row(reviewer_user_id=2, decision="APPROVE"),
    )
    monkeypatch.setattr(identity_recovery_module, "verify_password", lambda *_args: True)
    with pytest.raises(AppError) as captured:
        item.complete_case(
            actor=people[10], case_id=91, expected_version=4,
            current_password="target-password1", target_reauth_assertion=None, new_password=None,
            confirm_replace_phone=False, request_id="changed-impact",
        )
    assert captured.value.code == IdentityRecoveryErrorCode.CONFLICT
    item._repository.add_merge.assert_not_called()
    item._repository.add_phone.assert_not_called()
    item._repository.delete_password.assert_not_called()


def test_reviewer_authority_revocation_blocks_completion_before_locks() -> None:
    people = {1: principal(1, reviewer=True), 2: principal(2), 10: principal(10)}
    item = service(loader=people.__getitem__)
    record = row(
        id=91, requester_user_id=10, target_user_id=10, status="APPROVED",
        source_user_id=20, version=4, expires_at=NOW + timedelta(days=1), proof_verified_at=NOW,
    )
    item._repository.get_case_for_update.return_value = record
    item._repository.list_reviews.return_value = (
        row(reviewer_user_id=1, decision="APPROVE"),
        row(reviewer_user_id=2, decision="APPROVE"),
    )
    with pytest.raises(AppError) as captured:
        item.complete_case(
            actor=people[10], case_id=91, expected_version=4,
            current_password="target-password1", target_reauth_assertion=None, new_password=None,
            confirm_replace_phone=False, request_id="revoked-reviewer",
        )
    assert captured.value.code == IdentityRecoveryErrorCode.CONFLICT
    item._repository.lock_completion_rows.assert_not_called()


def test_target_without_password_requires_recent_bound_proof_and_sets_explicit_password(monkeypatch) -> None:
    people = {1: principal(1, reviewer=True), 2: principal(2, reviewer=True), 10: principal(10)}
    provider = IsolatedTestIdentityProofProvider(
        secret="local-target-proof", clock=lambda: NOW
    )
    item = service(loader=people.__getitem__, provider=provider)
    rows = completion_rows(target_has_password=False)
    record = approved_case(item, rows)
    item._repository.get_case_for_update.return_value = record
    item._repository.lock_completion_rows.return_value = rows
    item._repository.list_reviews.return_value = (
        row(reviewer_user_id=1, decision="APPROVE"),
        row(reviewer_user_id=2, decision="APPROVE"),
    )
    item._repository.add_merge.side_effect = lambda merge: (setattr(merge, "id", 502) or merge)
    item._repository.add_tombstone.side_effect = lambda value: value
    monkeypatch.setattr(identity_recovery_module, "hash_password", lambda value: f"target::{value}")
    with pytest.raises(AppError) as missing:
        item.complete_case(
            actor=people[10], case_id=91, expected_version=4,
            current_password=None, target_reauth_assertion=None,
            new_password="lowercase1", confirm_replace_phone=False,
            request_id="missing-target-proof",
        )
    assert missing.value.code == IdentityRecoveryErrorCode.PROOF_REJECTED
    token = provider.issue(
        purpose="TARGET_REAUTH:91",
        phone_e164="+8613900000123",
        subject_reference="synthetic-target-10",
    )
    result = item.complete_case(
        actor=people[10], case_id=91, expected_version=4,
        current_password=None, target_reauth_assertion=token, new_password="lowercase1",
        confirm_replace_phone=False, request_id="new-password-unit",
    )
    assert result.status == "COMPLETED"
    added = item._repository.add_password.call_args.args[0]
    assert added.user_id == 10
    assert added.password_hash == "target::lowercase1"
    assert added.password_hash != "source-hash"


def test_phone_reuse_never_moves_source_assets(monkeypatch) -> None:
    people = {1: principal(1, reviewer=True), 2: principal(2, reviewer=True), 10: principal(10)}
    item = service(loader=people.__getitem__)
    rows = completion_rows(purpose="PHONE_REUSE")
    record = approved_case(item, rows, purpose="PHONE_REUSE")
    item._repository.get_case_for_update.return_value = record
    item._repository.lock_completion_rows.return_value = rows
    item._repository.list_reviews.return_value = (
        row(reviewer_user_id=1, decision="APPROVE"),
        row(reviewer_user_id=2, decision="APPROVE"),
    )
    item._repository.add_phone.side_effect = lambda value: value
    item._repository.add_tombstone.side_effect = lambda value: value
    monkeypatch.setattr(identity_recovery_module, "verify_password", lambda *_args: True)
    result = item.complete_case(
        actor=people[10], case_id=91, expected_version=4,
        current_password="target-password1", target_reauth_assertion=None, new_password=None,
        confirm_replace_phone=True, request_id="reuse-unit",
    )
    assert result.status == "COMPLETED"
    assert rows.users[1].status == "ACTIVE"
    assert rows.files[0].uploader_user_id == 20
    assert rows.work_orders[0].reporter_user_id == 20
    assert item._repository.add_phone.call_args.args[0].phone_e164 == "+8613800000123"
    item._repository.add_merge.assert_not_called()


def test_account_recovery_accepts_historical_phone_without_reactivating_it(monkeypatch) -> None:
    people = {1: principal(1, reviewer=True), 2: principal(2, reviewer=True), 10: principal(10)}
    item = service(loader=people.__getitem__)
    rows = completion_rows(purpose="ACCOUNT_RECOVERY")
    record = approved_case(item, rows, purpose="ACCOUNT_RECOVERY")
    item._repository.get_case_for_update.return_value = record
    item._repository.lock_completion_rows.return_value = rows
    item._repository.list_reviews.return_value = (
        row(reviewer_user_id=1, decision="APPROVE"),
        row(reviewer_user_id=2, decision="APPROVE"),
    )
    item._repository.add_merge.side_effect = lambda merge: (setattr(merge, "id", 503) or merge)
    item._repository.add_tombstone.side_effect = lambda value: value
    monkeypatch.setattr(identity_recovery_module, "verify_password", lambda *_args: True)

    result = item.complete_case(
        actor=people[10], case_id=91, expected_version=4,
        current_password="target-password1", target_reauth_assertion=None,
        new_password=None, confirm_replace_phone=False,
        request_id="account-recovery-no-active-phone",
    )

    assert result.status == "COMPLETED"
    assert rows.phones[0].status == "ENDED"
    assert item._repository.add_merge.call_args.args[0].source_phone_id == 11


def test_same_id_role_scope_drift_invalidates_approved_impact(monkeypatch) -> None:
    people = {1: principal(1, reviewer=True), 2: principal(2, reviewer=True), 10: principal(10)}
    item = service(loader=people.__getitem__)
    rows = completion_rows()
    record = approved_case(item, rows)
    rows.roles[0].property_id = 99
    item._repository.get_case_for_update.return_value = record
    item._repository.lock_completion_rows.return_value = rows
    item._repository.list_reviews.return_value = (
        row(reviewer_user_id=1, decision="APPROVE"),
        row(reviewer_user_id=2, decision="APPROVE"),
    )
    monkeypatch.setattr(identity_recovery_module, "verify_password", lambda *_args: True)

    with pytest.raises(AppError) as captured:
        item.complete_case(
            actor=people[10], case_id=91, expected_version=4,
            current_password="target-password1", target_reauth_assertion=None,
            new_password=None, confirm_replace_phone=False,
            request_id="role-scope-drift",
        )

    assert captured.value.code == IdentityRecoveryErrorCode.CONFLICT
    item._repository.add_merge.assert_not_called()


@pytest.mark.parametrize("conflict", ["employee", "wechat", "house"])
def test_completion_preview_rejects_ambiguous_current_ownership(conflict: str) -> None:
    item = service()
    base = completion_rows()
    values = {field: getattr(base, field) for field in base.__dataclass_fields__}
    if conflict == "employee":
        values["employees"] += (row(id=72, user_id=10),)
    elif conflict == "wechat":
        values["wechats"] += (row(id=52, user_id=10, appid="source-app", unionid="target-union"),)
    else:
        values["relations"] += (
            row(id=82, user_id=10, house_id=1, status="ACTIVE", relation_type="TENANT", valid_to=None),
        )
    rows = IdentityCompletionRows(**values)
    record = row(
        source_user_id=20,
        target_user_id=10,
        subject_phone_e164="+8613800000123",
        purpose="MERGE_SOURCE_PROOF",
    )
    with pytest.raises(AppError) as captured:
        item._build_impact(record, rows)
    assert captured.value.code == IdentityRecoveryErrorCode.CONFLICT


def test_repository_uses_for_update_for_identity_completion_rows() -> None:
    session = MagicMock()
    session.scalars.return_value.all.return_value = ()
    from app.repository.identity_recovery import IdentityRecoveryRepository

    IdentityRecoveryRepository(session).lock_completion_rows(
        source_user_id=20,
        target_user_id=10,
        phone_e164="+8613800000123",
    )
    assert len(session.scalars.call_args_list) >= 15
    for call in session.scalars.call_args_list:
        sql = str(call.args[0].compile(dialect=mysql.dialect()))
        assert "FOR UPDATE" in sql
        assert call.args[0].get_execution_options()["populate_existing"] is True


def test_identity_write_guard_refreshes_locked_user_and_rejects_merged() -> None:
    session = MagicMock()
    session.scalar.return_value = row(id=20, status="MERGED")

    with pytest.raises(AppError) as captured:
        lock_active_identity(session, 20)

    assert captured.value.code == "IAM.FORBIDDEN"
    statement = session.scalar.call_args.args[0]
    assert "FOR UPDATE" in str(statement.compile(dialect=mysql.dialect()))
    assert statement.get_execution_options()["populate_existing"] is True


def test_direct_current_ownership_writers_guard_before_dependent_locks() -> None:
    work_orders = (REPO_ROOT / "backend/app/service/work_order.py").read_text(encoding="utf-8")
    create = work_orders[work_orders.index("    def _create_work_order("):]
    create = create[:create.index("\n    def ", 10)]
    assert create.index("lock_active_identity") < create.index("_reload_actor")

    employee_identity = (REPO_ROOT / "backend/app/service/employee_identity.py").read_text(encoding="utf-8")
    claim = employee_identity[employee_identity.index("    def claim("):]
    assert claim.index("lock_active_identity") < claim.index("get_employee_for_update")
    assert claim.index("lock_active_identity") < claim.index("with_for_update()")
    assert claim.index(".populate_existing()") < claim.index("with_for_update()")


def test_reset_ticket_lock_forces_identity_map_refresh() -> None:
    from app.repository.credential import CredentialRepository

    session = MagicMock()
    CredentialRepository(session).get_reset_ticket_for_update("ticket-hash")
    statement = session.scalar.call_args.args[0]
    assert "FOR UPDATE" in str(statement.compile(dialect=mysql.dialect()))
    assert statement.get_execution_options()["populate_existing"] is True


def test_resident_bind_request_lock_forces_identity_map_refresh() -> None:
    from app.repository.resident import ResidentBindingRepository

    session = MagicMock()
    ResidentBindingRepository(session).get_bind_request_for_update(71)
    statement = session.scalar.call_args.args[0]
    assert "FOR UPDATE" in str(statement.compile(dialect=mysql.dialect()))
    assert statement.get_execution_options()["populate_existing"] is True


def test_resident_approval_rejects_request_changed_while_waiting_for_user_lock() -> None:
    from app.core.errors import ResidentErrorCode
    from app.service.resident import ResidentBindingService

    item = ResidentBindingService.__new__(ResidentBindingService)
    item._repository = MagicMock()
    item._repository.get_bind_request_for_update.return_value = row(
        id=71, status="REJECTED"
    )

    with pytest.raises(AppError) as captured:
        item._lock_pending_request(71)

    assert captured.value.code == ResidentErrorCode.ALREADY_PROCESSED


def test_identity_page_separates_approval_from_completion_and_reauthentication() -> None:
    source = (REPO_ROOT / "miniprogram/pages/identity-recovery/identity-recovery.js").read_text(encoding="utf-8")
    template = (REPO_ROOT / "miniprogram/pages/identity-recovery/identity-recovery.wxml").read_text(encoding="utf-8")
    supervisor = (REPO_ROOT / "miniprogram/pages/supervisor/identity-recovery.wxml").read_text(encoding="utf-8")
    api = (REPO_ROOT / "miniprogram/services/api.js").read_text(encoding="utf-8")
    route = (REPO_ROOT / "backend/app/api/v1/identity_recovery.py").read_text(encoding="utf-8")

    assert "api.myIdentityRecoveryCases()" in source
    assert "api.completeIdentityRecoveryCase(id" in source
    assert "newPassword !== confirmPassword" in source
    assert "target_reauth_assertion" in source
    assert "confirm_replace_phone: item.purpose === \"PHONE_REUSE\" &&" in source
    assert "auth.clearSession()" in source
    assert "wx.reLaunch" in source
    assert "api.myIdentityRecoveryCases().then" not in source[source.index("complete(event)"):]
    assert "审核通过尚未改变账号" in template
    assert "目标账号当前密码" in template
    assert "目标身份核验令牌" in template
    assert "确认用本次号码替换" in template
    assert "确认并执行身份事务" in template
    assert "身份变更仍需申请人再次认证并执行" in supervisor
    assert "/identity/recovery/cases/${id}/complete" in api
    assert 'unauthorizedBusinessCodes: ["IDENTITY_RECOVERY.PROOF_REJECTED"]' in api
    assert '@router.post("/identity/recovery/cases/{case_id}/complete")' in route
