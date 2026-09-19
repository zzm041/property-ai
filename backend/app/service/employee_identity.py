"""Controlled employee invite and claim onto an existing Employee record."""

from datetime import timedelta

from sqlalchemy.orm import Session

from app.core.database import run_owned_transaction
from app.core.errors import AppError, AuthErrorCode, EmployeeErrorCode, IamErrorCode
from app.core.time import utc_now
from app.core.token_hash import generate_invite_token, keyed_hash
from app.domain.audit import AuditAction, AuditResourceType
from app.domain.employee import EMPLOYEE_MANAGE, EmployeeStatus
from app.domain.iam import OrganizationScope, Principal, ScopeType
from app.domain.phone import normalize_phone
from app.model.employee import Employee, EmployeeInvite
from app.model.identity import UserRole
from app.repository.credential import CredentialRepository
from app.repository.employee import EmployeeRepository
from app.repository.rbac_mutation import RbacMutationRepository
from app.service.audit import AuditService
from app.service.rbac import RbacService


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
            employee = self._employees.get_employee_for_update(peeked.employee_id)
            invite = (
                self._session.query(EmployeeInvite)
                .filter(EmployeeInvite.token_hash == token_hash)
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
            role = self._rbac.get_role_by_code("STAFF")
            if role is None:
                raise AppError(
                    code=IamErrorCode.ROLE_NOT_FOUND,
                    message="STAFF role was not found",
                    status_code=500,
                )
            assignments = self._employees.list_assignments_for_employee(employee.id)
            for assignment in assignments:
                if assignment.status != "ACTIVE":
                    continue
                self._session.add(
                    UserRole(
                        user_id=actor.user_id,
                        role_id=role.id,
                        scope_type=ScopeType.PROPERTY.value,
                        tenant_id=employee.tenant_id,
                        property_id=assignment.property_id,
                    )
                )
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
