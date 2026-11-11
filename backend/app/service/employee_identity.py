"""Controlled employee invite and claim onto an existing Employee record."""

from datetime import timedelta

from sqlalchemy import select, or_
from sqlalchemy.orm import Session

from app.core.database import run_owned_transaction, require_owned_transaction
from app.core.errors import AppError, AuthErrorCode, EmployeeErrorCode, IamErrorCode
from app.core.time import utc_now
from app.core.token_hash import generate_invite_token, keyed_hash
from app.domain.governance import AuditAction, AuditResourceType
from app.domain.work_order import EMPLOYEE_MANAGE, EmployeeStatus
from app.domain.identity import OrganizationScope, Principal, ScopeType
from app.domain.identity import normalize_phone
from app.model.employee import Employee, EmployeeInvite, EmployeeProperty
from app.repository.credential import CredentialRepository
from app.repository.employee import EmployeeRepository
from app.repository.rbac_mutation import RbacMutationRepository
from app.service.audit import AuditService
from app.service.identity.rbac import RbacService


def grant_claimed_staff_property(session: Session, *, employee: Employee, assignment,
                                actor_user_id: int, request_id: str) -> None:
    """Internal membership binding after authorized claim/appointment, not RBAC bypass.

    Both callers hold Employee FOR UPDATE and own the transaction. The fixed
    STAFF role is only for this effective Property; duty filtering stays separate.
    """
    from app.service.identity.rbac_mutation import RbacMutationService
    require_owned_transaction(session)
    now = utc_now()
    if (employee.user_id is None or employee.status != "ACTIVE"
            or assignment.tenant_id != employee.tenant_id or assignment.employee_id != employee.id
            or assignment.status != "ACTIVE" or assignment.valid_from > now
            or (assignment.valid_to is not None and assignment.valid_to <= now)):
        return
    scope = OrganizationScope(ScopeType.PROPERTY, employee.tenant_id, assignment.property_id)
    repository = RbacMutationRepository(session)
    role = repository.get_role_by_code("STAFF")
    if role is not None and repository.find_user_role_for_update(
        user_id=employee.user_id, role_id=role.id, scope=scope
    ) is not None:
        return
    RbacMutationService(session)._grant_user_role_in_current_transaction(
        actor_user_id=actor_user_id, target_user_id=employee.user_id, role_code="STAFF",
        scope=scope,
        request_id=request_id,
    )


def revoke_ended_staff_property(session: Session, *, employee: Employee, property_id: int,
                                actor_user_id: int, request_id: str) -> None:
    """End only this exact work scope; preserve all other roles and history."""
    from app.service.identity.rbac_mutation import RbacMutationService
    require_owned_transaction(session)
    if employee.user_id is None:
        return
    now = utc_now()
    remaining = session.scalar(select(EmployeeProperty.id).where(
        EmployeeProperty.employee_id == employee.id,
        EmployeeProperty.tenant_id == employee.tenant_id,
        EmployeeProperty.property_id == property_id,
        EmployeeProperty.status == "ACTIVE", EmployeeProperty.valid_from <= now,
        or_(EmployeeProperty.valid_to.is_(None), EmployeeProperty.valid_to > now),
    ).with_for_update())
    if remaining is not None:
        return
    scope = OrganizationScope(ScopeType.PROPERTY, employee.tenant_id, property_id)
    repository = RbacMutationRepository(session)
    role = repository.get_role_by_code("STAFF")
    if role is None or repository.find_user_role_for_update(
        user_id=employee.user_id, role_id=role.id, scope=scope
    ) is None:
        return
    RbacMutationService(session)._revoke_user_role_in_current_transaction(
        actor_user_id=actor_user_id, target_user_id=employee.user_id,
        role_code="STAFF", scope=scope, request_id=request_id,
    )


class EmployeeIdentityService:
    def __init__(
        self,
        session: Session,
        *,
        secret: str,
        employees: EmployeeRepository | None = None,
        credentials: CredentialRepository | None = None,
        rbac: RbacMutationRepository | None = None,
        audit: AuditService | None = None,
    ) -> None:
        self._session = session
        self._secret = secret
        self._employees = employees or EmployeeRepository(session)
        self._credentials = credentials or CredentialRepository(session)
        self._rbac = rbac or RbacMutationRepository(session)
        self._audit = audit or AuditService(session)

    def issue_invite(
        self,
        *,
        actor: Principal,
        employee_id: int,
        target_phone: str,
        request_id: str,
    ) -> str:
        phone = normalize_phone(target_phone)
        if phone is None:
            raise AppError(
                code=AuthErrorCode.INVALID_PHONE,
                message="Phone number is invalid",
                status_code=422,
            )
        token = generate_invite_token()
        now = utc_now()

        def _persist() -> None:
            employee = self._employees.get_employee_for_update(employee_id)
            if employee is None:
                raise AppError(
                    code=EmployeeErrorCode.EMPLOYEE_NOT_FOUND,
                    message="Employee was not found",
                    status_code=404,
                )
            RbacService.require_permission(
                actor,
                EMPLOYEE_MANAGE,
                OrganizationScope(
                    ScopeType.TENANT, tenant_id=employee.tenant_id
                ),
            )
            if employee.status != EmployeeStatus.ACTIVE.value:
                raise AppError(
                    code=EmployeeErrorCode.INVALID_INPUT,
                    message="Employee is not active",
                    status_code=409,
                )
            if employee.user_id is not None:
                raise AppError(
                    code=EmployeeErrorCode.INVALID_INPUT,
                    message="Employee is already bound",
                    status_code=409,
                )
            existing = (
                self._session.query(EmployeeInvite)
                .filter(
                    EmployeeInvite.employee_id == employee.id,
                    EmployeeInvite.status == "ACTIVE",
                )
                .with_for_update()
                .one_or_none()
            )
            if existing is not None:
                existing.status = "REVOKED"
                self._session.flush()
            invite = EmployeeInvite(
                employee_id=employee.id,
                tenant_id=employee.tenant_id,
                target_phone_e164=phone,
                token_hash=keyed_hash(token, secret=self._secret),
                inviter_user_id=actor.user_id,
                status="ACTIVE",
                expires_at=now + timedelta(days=7),
            )
            self._session.add(invite)
            self._session.flush()
            self._audit.append(
                action=AuditAction.EMPLOYEE_INVITE_ISSUED,
                resource_type=AuditResourceType.EMPLOYEE_INVITE,
                actor_user_id=actor.user_id,
                resource_id=invite.id,
                request_id=request_id,
                scope_type=ScopeType.TENANT,
                tenant_id=employee.tenant_id,
                details={"employee_id": employee.id},
            )

        run_owned_transaction(self._session, _persist)
        return token

    def claim(self, *, actor: Principal, token: str, request_id: str) -> Employee:
        token_hash = keyed_hash(token, secret=self._secret)
        holder: list[Employee] = []

        def _persist() -> None:
            peeked = (
                self._session.query(EmployeeInvite)
                .filter(EmployeeInvite.token_hash == token_hash)
                .one_or_none()
            )
            if peeked is None:
                raise AppError(
                    code=EmployeeErrorCode.INVALID_INPUT,
                    message="Invitation is invalid or expired",
                    status_code=400,
                )
            peeked_expires_at = getattr(peeked, "expires_at", None)
            if (
                getattr(peeked, "status", "ACTIVE") != "ACTIVE"
                or (peeked_expires_at is not None and peeked_expires_at <= utc_now())
            ):
                raise AppError(
                    code=EmployeeErrorCode.INVALID_INPUT,
                    message="Invitation is invalid or expired",
                    status_code=400,
                )
            from app.service.identity.identity_write_guard import lock_active_identity

            lock_active_identity(self._session, actor.user_id)
            employee = self._employees.get_employee_for_update(peeked.employee_id)
            invite = (
                self._session.query(EmployeeInvite)
                .filter(EmployeeInvite.token_hash == token_hash)
                .populate_existing()
                .with_for_update()
                .one_or_none()
            )
            now = utc_now()
            if invite is None or invite.status != "ACTIVE" or invite.expires_at <= now:
                raise AppError(
                    code=EmployeeErrorCode.INVALID_INPUT,
                    message="Invitation is invalid or expired",
                    status_code=400,
                )
            if invite.employee_id != peeked.employee_id:
                raise AppError(
                    code=EmployeeErrorCode.INVALID_INPUT,
                    message="Invitation is invalid or expired",
                    status_code=400,
                )
            phone = self._credentials.get_active_phone_for_user(actor.user_id)
            if phone is None or phone.phone_e164 != invite.target_phone_e164:
                raise AppError(
                    code=IamErrorCode.FORBIDDEN,
                    message="Permission denied",
                    status_code=403,
                )
            if employee is None or employee.status != EmployeeStatus.ACTIVE.value:
                raise AppError(
                    code=EmployeeErrorCode.INVALID_INPUT,
                    message="Employee is not available",
                    status_code=409,
                )
            if employee.user_id is not None:
                raise AppError(
                    code=EmployeeErrorCode.INVALID_INPUT,
                    message="Employee is already bound",
                    status_code=409,
                )
            employee.user_id = actor.user_id
            invite.status = "CONSUMED"
            invite.claimed_user_id = actor.user_id
            invite.consumed_at = now
            assignments = self._employees.list_assignments_for_employee(employee.id, lock=True)
            for assignment in assignments:
                grant_claimed_staff_property(self._session, employee=employee, assignment=assignment,
                                            actor_user_id=actor.user_id, request_id=request_id)
            self._audit.append(
                action=AuditAction.EMPLOYEE_CLAIMED,
                resource_type=AuditResourceType.EMPLOYEE,
                actor_user_id=actor.user_id,
                resource_id=employee.id,
                request_id=request_id,
                scope_type=ScopeType.TENANT,
                tenant_id=employee.tenant_id,
                details={"employee_id": employee.id, "user_id": actor.user_id},
            )
            holder.append(employee)

        run_owned_transaction(self._session, _persist)
        return holder[0]
