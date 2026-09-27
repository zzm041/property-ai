"""High-risk identity recovery request/response contracts."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


RecoveryPurpose = Literal["MERGE_SOURCE_PROOF", "ACCOUNT_RECOVERY", "PHONE_REUSE"]
RecoveryDecision = Literal["APPROVE", "REJECT"]


class IdentityRecoveryCapabilitiesData(BaseModel):
    schema_available: bool
    proof_provider_available: bool
    enabled: bool
    quarantine_days: int = 30
    required_distinct_reviewers: int = 2


class CreateIdentityRecoveryCaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    purpose: RecoveryPurpose
    phone: str = Field(min_length=8, max_length=20)
    proof_assertion: str = Field(min_length=8, max_length=2048)
    source_user_id: int | None = Field(default=None, strict=True, gt=0)


class ReviewIdentityRecoveryCaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: RecoveryDecision
    reason: str = Field(min_length=2, max_length=255)
    expected_version: int = Field(strict=True, ge=1)


class FinalizeIdentityRecoveryCaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(strict=True, ge=1)


class IdentityRecoveryCaseData(BaseModel):
    id: int
    purpose: RecoveryPurpose
    requester_user_id: int
    source_user_id: int | None
    target_user_id: int | None
    masked_phone: str
    proof_provider: str | None
    status: str
    quarantine_started_at: datetime
    quarantine_ends_at: datetime
    proof_verified_at: datetime | None
    review_completed_at: datetime | None
    expires_at: datetime
    completed_at: datetime | None
    attempt_count: int
    version: int
    approval_count: int = 0
