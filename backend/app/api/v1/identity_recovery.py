"""High-risk identity recovery and independent two-reviewer administration."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.api.dependencies.auth import get_current_principal
from app.api.dependencies.db import get_db_session
from app.core.config import Settings, get_settings
from app.domain.identity import Principal
from app.domain.identity import mask_phone
from app.repository.identity import IdentityRepository
from app.schema.common import request_id_for, success_envelope
from app.schema.identity_recovery import (
    CompleteIdentityRecoveryCaseRequest,
    CreateIdentityRecoveryCaseRequest,
    FinalizeIdentityRecoveryCaseRequest,
    IdentityRecoveryCapabilitiesData,
    IdentityRecoveryCaseData,
    ReviewIdentityRecoveryCaseRequest,
)
from app.service.identity.auth import AuthService
from app.service.identity.identity_recovery import IdentityRecoveryService
from app.provider.factory import build_identity_proof_provider


router = APIRouter(tags=["identity-recovery"])


def get_identity_recovery_service(
    session: Annotated[Session, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> IdentityRecoveryService:
    return IdentityRecoveryService(
        session,
        provider=build_identity_proof_provider(settings),
        principal_loader=AuthService(session, IdentityRepository(session)).load_principal,
    )


def _data(service: IdentityRecoveryService, record) -> dict[str, object]:
    return IdentityRecoveryCaseData(
        id=record.id,
        purpose=record.purpose,
        requester_user_id=record.requester_user_id,
        source_user_id=record.source_user_id,
        target_user_id=record.target_user_id,
        masked_phone=mask_phone(record.subject_phone_e164),
        proof_provider=record.proof_provider,
        status=record.status,
        quarantine_started_at=record.quarantine_started_at,
        quarantine_ends_at=record.quarantine_ends_at,
        proof_verified_at=record.proof_verified_at,
        review_completed_at=record.review_completed_at,
        expires_at=record.expires_at,
        completed_at=record.completed_at,
        attempt_count=record.attempt_count,
        version=record.version,
        approval_count=service.approval_count(record.id),
    ).model_dump(mode="json")


@router.get("/identity/recovery/capabilities")
def capabilities(
    request: Request,
    actor: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[IdentityRecoveryService, Depends(get_identity_recovery_service)],
) -> dict[str, object]:
    del actor
    return success_envelope(
        request,
        IdentityRecoveryCapabilitiesData.model_validate(service.capabilities()).model_dump(mode="json"),
    )


@router.post("/identity/recovery/cases")
def create_case(
    body: CreateIdentityRecoveryCaseRequest,
    request: Request,
    actor: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[IdentityRecoveryService, Depends(get_identity_recovery_service)],
) -> dict[str, object]:
    record = service.create_case(
        actor=actor,
        purpose=body.purpose,
        phone=body.phone,
        proof_assertion=body.proof_assertion,
        source_user_id=body.source_user_id,
        request_id=request_id_for(request),
    )
    return success_envelope(request, _data(service, record))


@router.get("/identity/recovery/cases/mine")
def list_my_cases(
    request: Request,
    actor: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[IdentityRecoveryService, Depends(get_identity_recovery_service)],
    after_id: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> dict[str, object]:
    records = service.list_my_cases(actor=actor, after_id=after_id, limit=limit)
    return success_envelope(request, {"items": [_data(service, item) for item in records]})


@router.post("/identity/recovery/cases/{case_id}/complete")
def complete_case(
    case_id: int,
    body: CompleteIdentityRecoveryCaseRequest,
    request: Request,
    actor: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[IdentityRecoveryService, Depends(get_identity_recovery_service)],
) -> dict[str, object]:
    record = service.complete_case(
        actor=actor,
        case_id=case_id,
        expected_version=body.expected_version,
        current_password=body.current_password,
        target_reauth_assertion=body.target_reauth_assertion,
        new_password=body.new_password,
        confirm_replace_phone=body.confirm_replace_phone,
        request_id=request_id_for(request),
    )
    return success_envelope(request, _data(service, record))


@router.get("/admin/identity-recovery/cases")
def list_cases(
    request: Request,
    actor: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[IdentityRecoveryService, Depends(get_identity_recovery_service)],
    status: str | None = None,
    after_id: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> dict[str, object]:
    records = service.list_cases(actor=actor, status=status, after_id=after_id, limit=limit)
    return success_envelope(request, {"items": [_data(service, item) for item in records]})


@router.post("/admin/identity-recovery/cases/{case_id}/reviews")
def review_case(
    case_id: int,
    body: ReviewIdentityRecoveryCaseRequest,
    request: Request,
    actor: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[IdentityRecoveryService, Depends(get_identity_recovery_service)],
) -> dict[str, object]:
    record = service.review_case(
        actor=actor,
        case_id=case_id,
        decision=body.decision,
        reason=body.reason,
        expected_version=body.expected_version,
        request_id=request_id_for(request),
    )
    return success_envelope(request, _data(service, record))


@router.post("/admin/identity-recovery/cases/{case_id}/finalize")
def finalize_case(
    case_id: int,
    body: FinalizeIdentityRecoveryCaseRequest,
    request: Request,
    actor: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[IdentityRecoveryService, Depends(get_identity_recovery_service)],
) -> dict[str, object]:
    record = service.finalize_case(
        actor=actor,
        case_id=case_id,
        expected_version=body.expected_version,
        request_id=request_id_for(request),
    )
    return success_envelope(request, _data(service, record))
