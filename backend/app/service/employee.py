"""Employee identity, property assignment, and atomic attendance use cases."""

from collections.abc import Callable
from datetime import date, datetime
from typing import TypeVar

from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.errors import (
    AppError,
    AuthErrorCode,
    EmployeeErrorCode,
    ErrorCode,
    IamErrorCode,
)
from app.core.time import current_business_date, to_business_date, utc_now
from app.domain.audit import AuditAction, AuditResourceType
from app.domain.employee import (
    ATTENDANCE_CHECK_IN,
    ATTENDANCE_CHECK_OUT,
    ATTENDANCE_READ,
    EMPLOYEE_MANAGE,
    EMPLOYEE_READ,
    AttendanceOnDuty,
    AttendanceSource,
    EmployeePropertyStatus,
    EmployeeStatus,
    assignment_is_currently_effective,
)
from app.domain.iam import OrganizationScope, Principal, ScopeType
from app.model.employee import (
    AttendanceRecord,
    Employee,
    EmployeeDailyStatus,
    EmployeeDuty,
    EmployeeProperty,
    EmployeeSkill,
)
from app.model.property import Property
from app.repository.employee import EmployeeRepository
from app.repository.identity import IdentityRepository
from app.repository.work_order import WorkOrderRepository
from app.service.audit import AuditService
from app.service.auth import AuthService
from app.service.rbac import RbacService

T = TypeVar("T")
_ACTIVE_ASSIGNMENT_UNIQUE = frozenset({"uq_employee_properties_active_property"})
_EMPLOYEE_USER_UNIQUE = frozenset({"uq_employees_user_id"})
_EMPLOYEE_NO_UNIQUE = frozenset({"uq_employees_tenant_employee_no"})
_ATTENDANCE_UNIQUE = frozenset({"uq_attendance_records_employee_property_date"})
_DAILY_UNIQUE = frozenset({"uq_employee_daily_statuses_employee_property_date"})
_DUTY_UNIQUE = frozenset({"uq_employee_duties_employee_duty"})
_SKILL_UNIQUE = frozenset({"uq_employee_skills_employee_skill"})
_SCOPE_FK = frozenset(
    {
        "fk_employee_properties_property_tenant_properties",
        "fk_attendance_records_property_tenant_properties",
        "fk_employee_daily_statuses_property_tenant_properties",
        "fk_employees_department_tenant_departments",
        "fk_employee_duties_duty_tenant_duties",
        "fk_employee_skills_skill_tenant_skills",
    }
)


class EmployeeService:
    """Own employee/attendance transactions; repositories never commit."""

    def __init__(
        self,
        session: Session,
        repository: EmployeeRepository | None = None,
        audit_service: AuditService | None = None,
        principal_loader: Callable[[int], Principal] | None = None,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._session = session
        self._repository = repository or EmployeeRepository(session)
        self._audit_service = audit_service or AuditService(session)
        self._principal_loader = principal_loader or AuthService(
            session,
            IdentityRepository(session),
        ).load_principal
        self._clock = clock

    def create_employee(
        self,
        *,
        actor: Principal,
        user_id: int,
        tenant_id: int,
        employee_no: str,
        property_id: int,
        request_id: str,
        department_id: int | None = None,
        duty_ids: tuple[int, ...] = (),
        skill_ids: tuple[int, ...] = (),
        max_capacity: int = 1,
    ) -> Employee:
        self._require_positive_id(user_id, "user_id")
        self._require_positive_id(tenant_id, "tenant_id")
        self._require_positive_id(property_id, "property_id")
        self._validate_employee_no(employee_no)
        self._validate_capacity(max_capacity)
        self._validate_request_id(request_id)
        return self._execute(
            lambda: self._create_employee(
                actor=actor,
                user_id=user_id,
                tenant_id=tenant_id,
                employee_no=employee_no.strip(),
                property_id=property_id,
                request_id=request_id,
                department_id=department_id,
                duty_ids=duty_ids,
                skill_ids=skill_ids,
                max_capacity=max_capacity,
            )
        )

    def update_employee(
        self,
        *,
        actor: Principal,
        employee_id: int,
        request_id: str,
        status: str | None = None,
        department_id: int | None = None,
        max_capacity: int | None = None,
    ) -> Employee:
        self._require_positive_id(employee_id, "employee_id")
        self._validate_request_id(request_id)
        return self._execute(
            lambda: self._update_employee(
                actor=actor,
                employee_id=employee_id,
                request_id=request_id,
                status=status,
                department_id=department_id,
                max_capacity=max_capacity,
            )
        )

    def get_employee(
        self,
        *,
        actor: Principal,
        employee_id: int,
    ) -> tuple[Employee, tuple[EmployeeProperty, ...]]:
        self._require_positive_id(employee_id, "employee_id")
        current = self._reload_actor(actor)
        employee = self._repository.get_employee(employee_id)
        if employee is None:
            raise AppError(
                code=EmployeeErrorCode.EMPLOYEE_NOT_FOUND,
                message="Employee was not found",
                status_code=404,
            )
        assignments = self._repository.list_assignments_for_employee(employee.id)
        self._require_employee_permission(
            current,
            EMPLOYEE_READ,
            employee,
            assignments,
        )
        return employee, assignments

    def assign_property(
        self,
        *,
        actor: Principal,
        employee_id: int,
        property_id: int,
        request_id: str,
    ) -> EmployeeProperty:
        self._require_positive_id(employee_id, "employee_id")
        self._require_positive_id(property_id, "property_id")
        self._validate_request_id(request_id)
        return self._execute(
            lambda: self._assign_property(
                actor=actor,
                employee_id=employee_id,
                property_id=property_id,
                request_id=request_id,
            )
        )

    def end_assignment(
        self,
        *,
        actor: Principal,
        assignment_id: int,
        request_id: str,
    ) -> EmployeeProperty:
        self._require_positive_id(assignment_id, "assignment_id")
        self._validate_request_id(request_id)
        return self._execute(
            lambda: self._end_assignment(
                actor=actor,
                assignment_id=assignment_id,
                request_id=request_id,
            )
        )

    def check_in(
        self,
        *,
        actor: Principal,
        property_id: int,
        request_id: str,
        claimed_tenant_id: int | None = None,
    ) -> tuple[AttendanceRecord, EmployeeDailyStatus]:
        self._require_positive_id(property_id, "property_id")
        self._validate_request_id(request_id)
        return self._execute(
            lambda: self._check_in(
                actor=actor,
                property_id=property_id,
                request_id=request_id,
                claimed_tenant_id=claimed_tenant_id,
            )
        )

    def check_out(
        self,
        *,
        actor: Principal,
        property_id: int,
        request_id: str,
        claimed_tenant_id: int | None = None,
    ) -> tuple[AttendanceRecord, EmployeeDailyStatus]:
        self._require_positive_id(property_id, "property_id")
        self._validate_request_id(request_id)
        return self._execute(
            lambda: self._check_out(
                actor=actor,
                property_id=property_id,
                request_id=request_id,
                claimed_tenant_id=claimed_tenant_id,
            )
        )

    def update_my_status(
        self,
        *,
        actor: Principal,
        property_id: int,
        on_duty: str,
        request_id: str,
        leave_type: str | None = None,
        claimed_tenant_id: int | None = None,
    ) -> tuple[AttendanceRecord, EmployeeDailyStatus]:
        self._require_positive_id(property_id, "property_id")
        self._validate_request_id(request_id)
        return self._execute(
            lambda: self._update_my_status(
                actor=actor,
                property_id=property_id,
                on_duty=on_duty,
                request_id=request_id,
                leave_type=leave_type,
                claimed_tenant_id=claimed_tenant_id,
            )
        )

    def get_my_today_status(
        self,
        *,
        actor: Principal,
        property_id: int,
        claimed_tenant_id: int | None = None,
    ) -> tuple[AttendanceRecord | None, EmployeeDailyStatus | None, date]:
        self._require_active_actor(actor)
        self._require_positive_id(property_id, "property_id")
        employee = self._require_own_employee(actor.user_id)
        property_record = self._require_property_in_employee_tenant(
            employee,
            property_id,
        )
        self._reject_claimed_tenant_mismatch(
            property_record.tenant_id,
            claimed_tenant_id,
        )
        business_date = current_business_date(
            property_record.timezone,
            clock=self._clock,
        )
        attendance = self._repository.get_attendance(
            employee_id=employee.id,
            property_id=property_record.id,
            business_date=business_date,
        )
        daily = self._repository.get_daily_status(
            employee_id=employee.id,
            property_id=property_record.id,
            business_date=business_date,
        )
        return attendance, daily, business_date

    def list_property_daily_status(
        self,
        *,
        actor: Principal,
        tenant_id: int,
        property_id: int,
        business_date: date | None = None,
        after_id: int = 0,
        limit: int = 50,
    ) -> tuple[tuple[EmployeeDailyStatus, ...], date]:
        self._require_positive_id(tenant_id, "tenant_id")
        self._require_positive_id(property_id, "property_id")
        current = self._reload_actor(actor)
        property_record = self._repository.get_property_in_tenant(
            tenant_id=tenant_id,
            property_id=property_id,
        )
        if property_record is None:
            raise AppError(
                code=IamErrorCode.FORBIDDEN,
                message="Permission denied",
                status_code=403,
            )
        RbacService.require_permission(
            current,
            ATTENDANCE_READ,
            OrganizationScope(
                ScopeType.PROPERTY,
                tenant_id=property_record.tenant_id,
                property_id=property_record.id,
            ),
        )
        resolved_date = business_date or current_business_date(
            property_record.timezone,
            clock=self._clock,
        )
        rows = self._repository.list_daily_statuses_for_property(
            tenant_id=property_record.tenant_id,
            property_id=property_record.id,
            business_date=resolved_date,
            after_id=max(after_id, 0),
            limit=self._page_limit(limit),
        )
        return rows, resolved_date

    def _create_employee(
        self,
        *,
        actor: Principal,
        user_id: int,
        tenant_id: int,
        employee_no: str,
        property_id: int,
        request_id: str,
        department_id: int | None,
        duty_ids: tuple[int, ...],
        skill_ids: tuple[int, ...],
        max_capacity: int,
    ) -> Employee:
        current = self._reload_actor(actor)
        property_record = self._require_managed_property(
            current,
            tenant_id=tenant_id,
            property_id=property_id,
            permission=EMPLOYEE_MANAGE,
        )
        user = IdentityRepository(self._session).get_user(user_id)
        if user is None or user.status != "ACTIVE":
            raise AppError(
                code=EmployeeErrorCode.INVALID_INPUT,
                message="Target user is not available",
                status_code=422,
                details={"field": "user_id"},
            )
        department = None
        if department_id is not None:
            self._require_positive_id(department_id, "department_id")
            department = self._repository.get_department_in_tenant(
                tenant_id=property_record.tenant_id,
                department_id=department_id,
            )
            if department is None:
                raise AppError(
                    code=EmployeeErrorCode.DEPARTMENT_NOT_FOUND,
                    message="Department was not found",
                    status_code=404,
                )
        employee = Employee(
            user_id=user.id,
            tenant_id=property_record.tenant_id,
            employee_no=employee_no,
            department_id=None if department is None else department.id,
            status=EmployeeStatus.ACTIVE.value,
            max_capacity=max_capacity,
        )
        try:
            self._repository.add_employee(employee)
        except IntegrityError as exc:
            self._map_insert_error(
                exc,
                expected=_EMPLOYEE_USER_UNIQUE | _EMPLOYEE_NO_UNIQUE,
            )
        self._link_duties_and_skills(
            employee,
            duty_ids=duty_ids,
            skill_ids=skill_ids,
        )
        now = self._clock()
        assignment = EmployeeProperty(
            employee_id=employee.id,
            tenant_id=employee.tenant_id,
            property_id=property_record.id,
            status=EmployeePropertyStatus.ACTIVE.value,
            valid_from=now,
            valid_to=None,
        )
        try:
            self._repository.add_assignment(assignment)
        except IntegrityError as exc:
            self._map_insert_error(exc, expected=_ACTIVE_ASSIGNMENT_UNIQUE)
        self._append_audit(
            action=AuditAction.EMPLOYEE_CREATED,
            resource_type=AuditResourceType.EMPLOYEE,
            actor_user_id=current.user_id,
            resource_id=employee.id,
            request_id=request_id,
            scope_type=ScopeType.TENANT,
            tenant_id=employee.tenant_id,
            details={"employee_no": employee.employee_no, "user_id": employee.user_id},
        )
        self._append_audit(
            action=AuditAction.EMPLOYEE_PROPERTY_ASSIGNED,
            resource_type=AuditResourceType.EMPLOYEE_PROPERTY,
            actor_user_id=current.user_id,
            resource_id=assignment.id,
            request_id=request_id,
            scope_type=ScopeType.PROPERTY,
            tenant_id=employee.tenant_id,
            property_id=property_record.id,
            details={
                "employee_id": employee.id,
                "property_id": property_record.id,
            },
        )
        return employee

    def _update_employee(
        self,
        *,
        actor: Principal,
        employee_id: int,
        request_id: str,
        status: str | None,
        department_id: int | None,
        max_capacity: int | None,
    ) -> Employee:
        current = self._reload_actor(actor)
        employee = self._repository.get_employee(employee_id)
        if employee is None:
            raise AppError(
                code=EmployeeErrorCode.EMPLOYEE_NOT_FOUND,
                message="Employee was not found",
                status_code=404,
            )
        assignments = self._repository.list_assignments_for_employee(employee.id)
        self._require_employee_permission(
            current,
            EMPLOYEE_MANAGE,
            employee,
            assignments,
        )
        if status is not None:
            try:
                employee.status = EmployeeStatus(status).value
            except ValueError as exc:
                raise AppError(
                    code=EmployeeErrorCode.INVALID_INPUT,
                    message="status is invalid",
                    status_code=422,
                    details={"field": "status"},
                ) from exc
        if department_id is not None:
            self._require_positive_id(department_id, "department_id")
            department = self._repository.get_department_in_tenant(
                tenant_id=employee.tenant_id,
                department_id=department_id,
            )
            if department is None:
                raise AppError(
                    code=EmployeeErrorCode.DEPARTMENT_NOT_FOUND,
                    message="Department was not found",
                    status_code=404,
                )
            employee.department_id = department.id
        if max_capacity is not None:
            self._validate_capacity(max_capacity)
            employee.max_capacity = max_capacity
        self._append_audit(
            action=AuditAction.EMPLOYEE_UPDATED,
            resource_type=AuditResourceType.EMPLOYEE,
            actor_user_id=current.user_id,
            resource_id=employee.id,
            request_id=request_id,
            scope_type=ScopeType.TENANT,
            tenant_id=employee.tenant_id,
            details={"status": employee.status},
        )
        return employee

    def _assign_property(
        self,
        *,
        actor: Principal,
        employee_id: int,
        property_id: int,
        request_id: str,
    ) -> EmployeeProperty:
        current = self._reload_actor(actor)
        employee = self._repository.get_employee(employee_id)
        if employee is None:
            raise AppError(
                code=EmployeeErrorCode.EMPLOYEE_NOT_FOUND,
                message="Employee was not found",
                status_code=404,
            )
        property_record = self._require_managed_property(
            current,
            tenant_id=employee.tenant_id,
            property_id=property_id,
            permission=EMPLOYEE_MANAGE,
        )
        assignment = EmployeeProperty(
            employee_id=employee.id,
            tenant_id=employee.tenant_id,
            property_id=property_record.id,
            status=EmployeePropertyStatus.ACTIVE.value,
            valid_from=self._clock(),
            valid_to=None,
        )
        try:
            self._repository.add_assignment(assignment)
        except IntegrityError as exc:
            self._map_insert_error(exc, expected=_ACTIVE_ASSIGNMENT_UNIQUE)
        self._append_audit(
            action=AuditAction.EMPLOYEE_PROPERTY_ASSIGNED,
            resource_type=AuditResourceType.EMPLOYEE_PROPERTY,
            actor_user_id=current.user_id,
            resource_id=assignment.id,
            request_id=request_id,
            scope_type=ScopeType.PROPERTY,
            tenant_id=employee.tenant_id,
            property_id=property_record.id,
            details={
                "employee_id": employee.id,
                "property_id": property_record.id,
            },
        )
        return assignment

    def _end_assignment(
        self,
        *,
        actor: Principal,
        assignment_id: int,
        request_id: str,
    ) -> EmployeeProperty:
        current = self._reload_actor(actor)
        assignment = self._repository.get_assignment_for_update(assignment_id)
        if assignment is None:
            raise AppError(
                code=EmployeeErrorCode.ASSIGNMENT_NOT_FOUND,
                message="Assignment was not found",
                status_code=404,
            )
        RbacService.require_permission(
            current,
            EMPLOYEE_MANAGE,
            OrganizationScope(
                ScopeType.PROPERTY,
                tenant_id=assignment.tenant_id,
                property_id=assignment.property_id,
            ),
        )
        if assignment.status != EmployeePropertyStatus.ACTIVE.value:
            raise AppError(
                code=EmployeeErrorCode.CONFLICT,
                message="Assignment is not active",
                status_code=409,
            )
        self._repository.end_assignment(
            assignment,
            ended_at=self._clock(),
            status=EmployeePropertyStatus.ENDED.value,
        )
        self._append_audit(
            action=AuditAction.EMPLOYEE_PROPERTY_ENDED,
            resource_type=AuditResourceType.EMPLOYEE_PROPERTY,
            actor_user_id=current.user_id,
            resource_id=assignment.id,
            request_id=request_id,
            scope_type=ScopeType.PROPERTY,
            tenant_id=assignment.tenant_id,
            property_id=assignment.property_id,
            details={
                "employee_id": assignment.employee_id,
                "property_id": assignment.property_id,
            },
        )
        return assignment

    def _check_in(
        self,
        *,
        actor: Principal,
        property_id: int,
        request_id: str,
        claimed_tenant_id: int | None,
    ) -> tuple[AttendanceRecord, EmployeeDailyStatus]:
        current = self._reload_actor(actor)
        employee = self._require_own_active_employee(current.user_id)
        property_record = self._require_property_in_employee_tenant(
            employee,
            property_id,
        )
        self._reject_claimed_tenant_mismatch(
            property_record.tenant_id,
            claimed_tenant_id,
        )
        now = self._clock()
        self._require_effective_assignment(employee, property_record, now)
        RbacService.require_permission(
            current,
            ATTENDANCE_CHECK_IN,
            OrganizationScope(
                ScopeType.PROPERTY,
                tenant_id=property_record.tenant_id,
                property_id=property_record.id,
            ),
        )
        business_date = to_business_date(now, property_record.timezone)
        existing = self._repository.get_attendance_for_update(
            employee_id=employee.id,
            property_id=property_record.id,
            business_date=business_date,
        )
        if existing is not None and existing.check_out_at is None:
            raise AppError(
                code=EmployeeErrorCode.CHECKIN_DENIED,
                message="Check-in was denied",
                status_code=409,
            )
        if existing is not None:
            daily = self._repository.get_daily_status_for_update(
                employee_id=employee.id,
                property_id=property_record.id,
                business_date=business_date,
            )
            if daily is None:
                raise AppError(
                    code=EmployeeErrorCode.CHECKIN_DENIED,
                    message="Check-in was denied",
                    status_code=409,
                )
            existing.check_out_at = None
            daily.on_duty = AttendanceOnDuty.ON_DUTY.value
            daily.leave_type = None
            self._append_audit(
                action=AuditAction.ATTENDANCE_CHECKED_IN,
                resource_type=AuditResourceType.ATTENDANCE_RECORD,
                actor_user_id=current.user_id,
                resource_id=existing.id,
                request_id=request_id,
                scope_type=ScopeType.PROPERTY,
                tenant_id=employee.tenant_id,
                property_id=property_record.id,
                details={
                    "employee_id": employee.id,
                    "property_id": property_record.id,
                    "business_date": business_date.isoformat(),
                },
            )
            return existing, daily
        attendance = AttendanceRecord(
            employee_id=employee.id,
            tenant_id=employee.tenant_id,
            property_id=property_record.id,
            business_date=business_date,
            check_in_at=now,
            check_out_at=None,
            source=AttendanceSource.STAFF_SELF.value,
        )
        daily = EmployeeDailyStatus(
            employee_id=employee.id,
            tenant_id=employee.tenant_id,
            property_id=property_record.id,
            business_date=business_date,
            on_duty=AttendanceOnDuty.ON_DUTY.value,
            leave_type=None,
            current_load=WorkOrderRepository(self._session).count_active_assignments_for_employee(
                employee_id=employee.id,
                property_id=property_record.id,
            ),
        )
        try:
            self._repository.add_attendance(attendance)
            self._repository.add_daily_status(daily)
        except IntegrityError as exc:
            self._map_insert_error(
                exc,
                expected=_ATTENDANCE_UNIQUE | _DAILY_UNIQUE,
            )
        self._append_audit(
            action=AuditAction.ATTENDANCE_CHECKED_IN,
            resource_type=AuditResourceType.ATTENDANCE_RECORD,
            actor_user_id=current.user_id,
            resource_id=attendance.id,
            request_id=request_id,
            scope_type=ScopeType.PROPERTY,
            tenant_id=employee.tenant_id,
            property_id=property_record.id,
            details={
                "employee_id": employee.id,
                "property_id": property_record.id,
                "business_date": business_date.isoformat(),
            },
        )
        return attendance, daily

    def _check_out(
        self,
        *,
        actor: Principal,
        property_id: int,
        request_id: str,
        claimed_tenant_id: int | None,
    ) -> tuple[AttendanceRecord, EmployeeDailyStatus]:
        current = self._reload_actor(actor)
        employee = self._require_own_active_employee(
            current.user_id,
            denied_code=EmployeeErrorCode.CHECKOUT_DENIED,
        )
        property_record = self._require_property_in_employee_tenant(
            employee,
            property_id,
            denied_code=EmployeeErrorCode.CHECKOUT_DENIED,
        )
        self._reject_claimed_tenant_mismatch(
            property_record.tenant_id,
            claimed_tenant_id,
        )
        now = self._clock()
        self._require_effective_assignment(
            employee,
            property_record,
            now,
            denied_code=EmployeeErrorCode.CHECKOUT_DENIED,
        )
        RbacService.require_permission(
            current,
            ATTENDANCE_CHECK_OUT,
            OrganizationScope(
                ScopeType.PROPERTY,
                tenant_id=property_record.tenant_id,
                property_id=property_record.id,
            ),
        )
        business_date = to_business_date(now, property_record.timezone)
        attendance = self._repository.get_attendance_for_update(
            employee_id=employee.id,
            property_id=property_record.id,
            business_date=business_date,
        )
        daily = self._repository.get_daily_status_for_update(
            employee_id=employee.id,
            property_id=property_record.id,
            business_date=business_date,
        )
        if (
            attendance is None
            or attendance.check_out_at is not None
            or daily is None
        ):
            raise AppError(
                code=EmployeeErrorCode.CHECKOUT_DENIED,
                message="Check-out was denied",
                status_code=409,
            )
        attendance.check_out_at = now
        daily.on_duty = AttendanceOnDuty.OFF_DUTY.value
        self._append_audit(
            action=AuditAction.ATTENDANCE_CHECKED_OUT,
            resource_type=AuditResourceType.ATTENDANCE_RECORD,
            actor_user_id=current.user_id,
            resource_id=attendance.id,
            request_id=request_id,
            scope_type=ScopeType.PROPERTY,
            tenant_id=employee.tenant_id,
            property_id=property_record.id,
            details={
                "employee_id": employee.id,
                "property_id": property_record.id,
                "business_date": business_date.isoformat(),
            },
        )
        return attendance, daily

    def _update_my_status(
        self,
        *,
        actor: Principal,
        property_id: int,
        on_duty: str,
        request_id: str,
        leave_type: str | None,
        claimed_tenant_id: int | None,
    ) -> tuple[AttendanceRecord, EmployeeDailyStatus]:
        current = self._reload_actor(actor)
        employee = self._require_own_active_employee(current.user_id)
        property_record = self._require_property_in_employee_tenant(
            employee,
            property_id,
        )
        self._reject_claimed_tenant_mismatch(
            property_record.tenant_id,
            claimed_tenant_id,
        )
        now = self._clock()
        self._require_effective_assignment(employee, property_record, now)
        RbacService.require_permission(
            current,
            ATTENDANCE_CHECK_IN,
            OrganizationScope(
                ScopeType.PROPERTY,
                tenant_id=property_record.tenant_id,
                property_id=property_record.id,
            ),
        )
        try:
            resolved = AttendanceOnDuty(on_duty)
        except ValueError as exc:
            raise AppError(
                code=EmployeeErrorCode.INVALID_INPUT,
                message="on_duty is invalid",
                status_code=422,
                details={"field": "on_duty"},
            ) from exc
        if resolved is AttendanceOnDuty.OFF_DUTY:
            raise AppError(
                code=EmployeeErrorCode.INVALID_INPUT,
                message="Use check-out to leave duty",
                status_code=422,
                details={"field": "on_duty"},
            )
        if resolved is AttendanceOnDuty.LEAVE:
            if leave_type is None or not leave_type.strip() or len(leave_type) > 32:
                raise AppError(
                    code=EmployeeErrorCode.INVALID_INPUT,
                    message="leave_type is required for leave",
                    status_code=422,
                    details={"field": "leave_type"},
                )
            resolved_leave = leave_type.strip()
        else:
            if leave_type is not None:
                raise AppError(
                    code=EmployeeErrorCode.INVALID_INPUT,
                    message="leave_type is only allowed for leave",
                    status_code=422,
                    details={"field": "leave_type"},
                )
            resolved_leave = None
        business_date = to_business_date(now, property_record.timezone)
        attendance = self._repository.get_attendance_for_update(
            employee_id=employee.id,
            property_id=property_record.id,
            business_date=business_date,
        )
        daily = self._repository.get_daily_status_for_update(
            employee_id=employee.id,
            property_id=property_record.id,
            business_date=business_date,
        )
        if (
            attendance is None
            or attendance.check_out_at is not None
            or daily is None
        ):
            raise AppError(
                code=EmployeeErrorCode.CHECKIN_DENIED,
                message="Check-in was denied",
                status_code=409,
            )
        daily.on_duty = resolved.value
        daily.leave_type = resolved_leave
        self._append_audit(
            action=AuditAction.ATTENDANCE_STATUS_UPDATED,
            resource_type=AuditResourceType.EMPLOYEE_DAILY_STATUS,
            actor_user_id=current.user_id,
            resource_id=daily.id,
            request_id=request_id,
            scope_type=ScopeType.PROPERTY,
            tenant_id=employee.tenant_id,
            property_id=property_record.id,
            details={
                "employee_id": employee.id,
                "property_id": property_record.id,
                "business_date": business_date.isoformat(),
                "on_duty": daily.on_duty,
            },
        )
        return attendance, daily

    def _link_duties_and_skills(
        self,
        employee: Employee,
        *,
        duty_ids: tuple[int, ...],
        skill_ids: tuple[int, ...],
    ) -> None:
        for duty_id in duty_ids:
            self._require_positive_id(duty_id, "duty_id")
            duty = self._repository.get_duty_in_tenant(
                tenant_id=employee.tenant_id,
                duty_id=duty_id,
            )
            if duty is None:
                raise AppError(
                    code=EmployeeErrorCode.DUTY_NOT_FOUND,
                    message="Duty was not found",
                    status_code=404,
                )
            try:
                self._repository.add_employee_duty(
                    EmployeeDuty(
                        employee_id=employee.id,
                        tenant_id=employee.tenant_id,
                        duty_id=duty.id,
                    )
                )
            except IntegrityError as exc:
                self._map_insert_error(exc, expected=_DUTY_UNIQUE)
        for skill_id in skill_ids:
            self._require_positive_id(skill_id, "skill_id")
            skill = self._repository.get_skill_in_tenant(
                tenant_id=employee.tenant_id,
                skill_id=skill_id,
            )
            if skill is None:
                raise AppError(
                    code=EmployeeErrorCode.SKILL_NOT_FOUND,
                    message="Skill was not found",
                    status_code=404,
                )
            try:
                self._repository.add_employee_skill(
                    EmployeeSkill(
                        employee_id=employee.id,
                        tenant_id=employee.tenant_id,
                        skill_id=skill.id,
                    )
                )
            except IntegrityError as exc:
                self._map_insert_error(exc, expected=_SKILL_UNIQUE)

    def _require_managed_property(
        self,
        actor: Principal,
        *,
        tenant_id: int,
        property_id: int,
        permission: str,
    ) -> Property:
        property_record = self._repository.get_property_in_tenant(
            tenant_id=tenant_id,
            property_id=property_id,
        )
        if property_record is None:
            raise AppError(
                code=IamErrorCode.FORBIDDEN,
                message="Permission denied",
                status_code=403,
            )
        RbacService.require_permission(
            actor,
            permission,
            OrganizationScope(
                ScopeType.PROPERTY,
                tenant_id=property_record.tenant_id,
                property_id=property_record.id,
            ),
        )
        return property_record

    def _require_own_employee(self, user_id: int) -> Employee:
        employee = self._repository.get_employee_for_user(user_id)
        if employee is None:
            raise AppError(
                code=EmployeeErrorCode.EMPLOYEE_NOT_FOUND,
                message="Employee was not found",
                status_code=404,
            )
        return employee

    def _require_own_active_employee(
        self,
        user_id: int,
        *,
        denied_code: str = EmployeeErrorCode.CHECKIN_DENIED,
    ) -> Employee:
        employee = self._require_own_employee(user_id)
        if employee.status != EmployeeStatus.ACTIVE.value:
            raise AppError(
                code=denied_code,
                message="Attendance was denied",
                status_code=409,
            )
        return employee

    def _require_property_in_employee_tenant(
        self,
        employee: Employee,
        property_id: int,
        *,
        denied_code: str = EmployeeErrorCode.CHECKIN_DENIED,
    ) -> Property:
        property_record = self._repository.get_property_in_tenant(
            tenant_id=employee.tenant_id,
            property_id=property_id,
        )
        if property_record is None:
            raise AppError(
                code=denied_code,
                message="Attendance was denied",
                status_code=409,
            )
        return property_record

    def _require_employee_permission(
        self,
        actor: Principal,
        permission: str,
        employee: Employee,
        assignments: tuple[EmployeeProperty, ...],
    ) -> None:
        tenant_scope = OrganizationScope(
            ScopeType.TENANT,
            tenant_id=employee.tenant_id,
        )
        if actor.has_permission(permission, tenant_scope):
            return
        for assignment in assignments:
            if actor.has_permission(
                permission,
                OrganizationScope(
                    ScopeType.PROPERTY,
                    tenant_id=employee.tenant_id,
                    property_id=assignment.property_id,
                ),
            ):
                return
        raise AppError(
            code=IamErrorCode.FORBIDDEN,
            message="Permission denied",
            status_code=403,
        )

    def _require_effective_assignment(
        self,
        employee: Employee,
        property_record: Property,
        now: datetime,
        *,
        denied_code: str = EmployeeErrorCode.CHECKIN_DENIED,
    ) -> EmployeeProperty:
        assignment = self._repository.find_effective_assignment(
            employee_id=employee.id,
            property_id=property_record.id,
            now=now,
        )
        if assignment is None or not assignment_is_currently_effective(
            status=assignment.status,
            valid_from=assignment.valid_from,
            valid_to=assignment.valid_to,
            now=now,
        ):
            raise AppError(
                code=denied_code,
                message="Attendance was denied",
                status_code=409,
            )
        return assignment

    @staticmethod
    def _reject_claimed_tenant_mismatch(
        tenant_id: int,
        claimed_tenant_id: int | None,
    ) -> None:
        if claimed_tenant_id is not None and claimed_tenant_id != tenant_id:
            raise AppError(
                code=EmployeeErrorCode.INVALID_INPUT,
                message="Property does not match the claimed tenant",
                status_code=422,
            )

    def _reload_actor(self, actor: Principal) -> Principal:
        if not isinstance(actor, Principal) or actor.user_id <= 0:
            raise AppError(
                code=IamErrorCode.FORBIDDEN,
                message="Permission denied",
                status_code=403,
            )
        try:
            current = self._principal_loader(actor.user_id)
        except AppError as exc:
            raise AppError(
                code=IamErrorCode.FORBIDDEN,
                message="Permission denied",
                status_code=403,
            ) from exc
        if current.user_id != actor.user_id or current.status != "ACTIVE":
            raise AppError(
                code=IamErrorCode.FORBIDDEN,
                message="Permission denied",
                status_code=403,
            )
        return current

    @staticmethod
    def _require_active_actor(actor: Principal) -> None:
        if (
            not isinstance(actor, Principal)
            or actor.user_id <= 0
            or actor.status != "ACTIVE"
        ):
            raise AppError(
                code=AuthErrorCode.USER_DISABLED
                if getattr(actor, "status", None) not in {None, "ACTIVE"}
                else IamErrorCode.FORBIDDEN,
                message="Permission denied",
                status_code=401
                if getattr(actor, "status", None) not in {None, "ACTIVE"}
                else 403,
            )

    def _execute(self, operation: Callable[[], T]) -> T:
        if self._session.in_transaction():
            self._session.rollback()
        try:
            with self._session.begin():
                return operation()
        except AppError:
            raise
        except IntegrityError as exc:
            self._map_insert_error(
                exc,
                expected=_ACTIVE_ASSIGNMENT_UNIQUE
                | _EMPLOYEE_USER_UNIQUE
                | _EMPLOYEE_NO_UNIQUE
                | _ATTENDANCE_UNIQUE
                | _DAILY_UNIQUE,
            )
            raise
        except SQLAlchemyError as exc:
            raise AppError(
                code=ErrorCode.INTERNAL_ERROR,
                message="Employee operation could not be completed",
                status_code=500,
            ) from exc

    def _map_insert_error(
        self,
        error: IntegrityError,
        *,
        expected: frozenset[str],
    ) -> None:
        arguments = getattr(error.orig, "args", ())
        number = arguments[0] if arguments else None
        message = str(arguments[1]).casefold() if len(arguments) > 1 else ""
        if number == 1062:
            if any(name.casefold() in message for name in _EMPLOYEE_USER_UNIQUE & expected):
                raise AppError(
                    code=EmployeeErrorCode.DUPLICATE_EMPLOYEE,
                    message="Employee identity already exists",
                    status_code=409,
                ) from error
            if any(name.casefold() in message for name in _EMPLOYEE_NO_UNIQUE & expected):
                raise AppError(
                    code=EmployeeErrorCode.DUPLICATE_EMPLOYEE,
                    message="Employee identity already exists",
                    status_code=409,
                ) from error
            if any(
                name.casefold() in message
                for name in _ACTIVE_ASSIGNMENT_UNIQUE & expected
            ):
                raise AppError(
                    code=EmployeeErrorCode.DUPLICATE_ASSIGNMENT,
                    message="An active property assignment already exists",
                    status_code=409,
                ) from error
            if any(
                name.casefold() in message
                for name in (_ATTENDANCE_UNIQUE | _DAILY_UNIQUE) & expected
            ):
                raise AppError(
                    code=EmployeeErrorCode.CHECKIN_DENIED,
                    message="Check-in was denied",
                    status_code=409,
                ) from error
            raise AppError(
                code=EmployeeErrorCode.CONFLICT,
                message="Employee data conflicted with current data",
                status_code=409,
            ) from error
        if number == 1452 and any(name.casefold() in message for name in _SCOPE_FK):
            raise AppError(
                code=EmployeeErrorCode.INVALID_INPUT,
                message="Related resource was not found",
                status_code=422,
            ) from error
        if number == 1213:
            raise AppError(
                code=EmployeeErrorCode.CONFLICT,
                message="Employee data conflicted with current data",
                status_code=409,
            ) from error
        raise AppError(
            code=EmployeeErrorCode.CONFLICT,
            message="Employee data conflicted with current data",
            status_code=409,
        ) from error

    def _append_audit(self, **values: object) -> None:
        try:
            self._audit_service.append(**values)
        except AppError:
            raise
        except SQLAlchemyError as exc:
            raise AppError(
                code=ErrorCode.INTERNAL_ERROR,
                message="Employee audit could not be written",
                status_code=500,
            ) from exc

    @staticmethod
    def _validate_employee_no(value: str) -> None:
        if not isinstance(value, str) or not value.strip() or len(value.strip()) > 32:
            raise AppError(
                code=EmployeeErrorCode.INVALID_INPUT,
                message="employee_no is invalid",
                status_code=422,
                details={"field": "employee_no"},
            )

    @staticmethod
    def _validate_capacity(value: int) -> None:
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise AppError(
                code=EmployeeErrorCode.INVALID_INPUT,
                message="max_capacity is invalid",
                status_code=422,
                details={"field": "max_capacity"},
            )

    @staticmethod
    def _require_positive_id(value: int, field: str) -> None:
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise AppError(
                code=EmployeeErrorCode.INVALID_INPUT,
                message=f"{field} must be a positive integer",
                status_code=422,
                details={"field": field},
            )

    @staticmethod
    def _validate_request_id(request_id: str) -> None:
        if (
            not isinstance(request_id, str)
            or not request_id
            or request_id != request_id.strip()
            or len(request_id) > 128
        ):
            raise AppError(
                code=EmployeeErrorCode.CONFLICT,
                message="Employee data conflicted with current data",
                status_code=409,
            )

    @staticmethod
    def _page_limit(limit: int) -> int:
        if limit < 1:
            return 1
        return min(limit, 100)
