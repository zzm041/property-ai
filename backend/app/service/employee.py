"""Employee identity, property assignment, and atomic attendance use cases."""

from collections.abc import Callable
from datetime import date, datetime
import re
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
from app.domain.governance import AuditAction, AuditResourceType
from app.domain.work_order import (
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
from app.domain.identity import OrganizationScope, Principal, ScopeType
from app.model.employee import (
    AttendanceRecord,
    Department,
    Employee,
    EmployeeDailyStatus,
    EmployeeDuty,
    EmployeeProperty,
    EmployeeSkill,
    Duty,
    Skill,
)
from app.model.property import Property
from app.repository.employee import EmployeeRepository
from app.repository.identity import IdentityRepository
from app.repository.work_order import WorkOrderRepository
from app.service.audit import AuditService
from app.service.identity.auth import AuthService
from app.service.identity.rbac import RbacService

T = TypeVar("T")
_ACTIVE_ASSIGNMENT_UNIQUE = frozenset({"uq_employee_properties_active_property"})
_EMPLOYEE_USER_UNIQUE = frozenset({"uq_employees_user_id"})
_EMPLOYEE_NO_UNIQUE = frozenset({"uq_employees_tenant_employee_no"})
_ATTENDANCE_UNIQUE = frozenset({"uq_attendance_records_employee_property_date"})
_DAILY_UNIQUE = frozenset({"uq_employee_daily_statuses_employee_property_date"})
_DUTY_UNIQUE = frozenset({"uq_employee_duties_employee_duty"})
_SKILL_UNIQUE = frozenset({"uq_employee_skills_employee_skill"})
_DUTY_CODE_UNIQUE = frozenset({"uq_duties_tenant_code"})
_SKILL_CODE_UNIQUE = frozenset({"uq_skills_tenant_code"})
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
        user_id: int | None,
        tenant_id: int,
        employee_no: str,
        property_id: int,
        request_id: str,
        department_id: int | None = None,
        duty_ids: tuple[int, ...] = (),
        skill_ids: tuple[int, ...] = (),
        max_capacity: int = 1,
    ) -> Employee:
        if user_id is not None:
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
        add_duty_ids: tuple[int, ...] | None = None,
        add_skill_ids: tuple[int, ...] | None = None,
        clear_department: bool = False,
    ) -> Employee:
        self._require_positive_id(employee_id, "employee_id")
        self._validate_request_id(request_id)
        return self._execute_availability(
            lambda: self._update_employee(
                actor=actor,
                employee_id=employee_id,
                request_id=request_id,
                status=status,
                department_id=department_id,
                max_capacity=max_capacity,
                add_duty_ids=add_duty_ids,
                add_skill_ids=add_skill_ids,
                clear_department=clear_department,
            )
        )

    def create_duty(
        self,
        *,
        actor: Principal,
        tenant_id: int,
        property_id: int,
        code: str,
        name: str,
        request_id: str,
    ) -> Duty:
        self._validate_catalog_item(code=code, name=name)
        self._validate_request_id(request_id)
        return self._execute(
            lambda: self._create_catalog_item(
                actor=actor,
                tenant_id=tenant_id,
                property_id=property_id,
                code=code.strip().upper(),
                name=name.strip(),
                request_id=request_id,
                kind="DUTY",
            )
        )

    def create_skill(
        self,
        *,
        actor: Principal,
        tenant_id: int,
        property_id: int,
        code: str,
        name: str,
        request_id: str,
    ) -> Skill:
        self._validate_catalog_item(code=code, name=name)
        self._validate_request_id(request_id)
        return self._execute(
            lambda: self._create_catalog_item(
                actor=actor,
                tenant_id=tenant_id,
                property_id=property_id,
                code=code.strip().upper(),
                name=name.strip(),
                request_id=request_id,
                kind="SKILL",
            )
        )

    def list_catalog(
        self,
        *,
        actor: Principal,
        tenant_id: int,
        property_id: int,
    ) -> tuple[tuple[Department, ...], tuple[Duty, ...], tuple[Skill, ...]]:
        current = self._reload_actor(actor)
        property_record = self._require_managed_property(
            current,
            tenant_id=tenant_id,
            property_id=property_id,
            permission=EMPLOYEE_READ,
        )
        return (
            self._repository.list_departments_for_property(
                tenant_id=property_record.tenant_id,
                property_id=property_record.id,
            ),
            self._repository.list_duties_for_tenant(property_record.tenant_id),
            self._repository.list_skills_for_tenant(property_record.tenant_id),
        )

    def list_employees(
        self,
        *,
        actor: Principal,
        tenant_id: int,
        property_id: int,
        after_id: int = 0,
        limit: int = 50,
        include_historical: bool = False,
    ) -> tuple[
        tuple[
            Employee,
            tuple[EmployeeProperty, ...],
            tuple[Duty, ...],
            tuple[Skill, ...],
        ],
        ...,
    ]:
        self._require_positive_id(tenant_id, "tenant_id")
        self._require_positive_id(property_id, "property_id")
        current = self._reload_actor(actor)
        property_record = self._require_managed_property(
            current,
            tenant_id=tenant_id,
            property_id=property_id,
            permission=EMPLOYEE_READ,
        )
        employees = self._repository.list_employees_for_property(
            tenant_id=property_record.tenant_id,
            property_id=property_record.id,
            now=self._clock(),
            after_id=max(after_id, 0),
            limit=self._page_limit(limit),
            include_historical=include_historical,
        )
        return tuple(
            (
                employee,
                self._visible_employee_assignments(
                    current,
                    employee,
                    self._repository.list_assignments_for_employee(employee.id),
                    permission=EMPLOYEE_READ,
                ),
                self._repository.list_duties_for_employee(employee.id),
                self._repository.list_skills_for_employee(employee.id),
            )
            for employee in employees
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
        return employee, self._visible_employee_assignments(
            current,
            employee,
            assignments,
            permission=EMPLOYEE_READ,
        )

    def get_employee_catalog_links(
        self,
        *,
        actor: Principal,
        employee_id: int,
    ) -> tuple[tuple[Duty, ...], tuple[Skill, ...]]:
        employee, _assignments = self.get_employee(
            actor=actor,
            employee_id=employee_id,
        )
        return (
            self._repository.list_duties_for_employee(employee.id),
            self._repository.list_skills_for_employee(employee.id),
        )

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
        return self._execute_availability(
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
        return self._execute_availability(
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
        return self._execute_availability(
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
        return self._execute_availability(
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
        return self._execute_availability(
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
        user_id: int | None,
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
        resolved_user_id = None
        if user_id is not None:
            from app.service.identity.identity_write_guard import lock_active_identity

            try:
                user = lock_active_identity(self._session, user_id)
            except AppError as exc:
                raise AppError(
                    code=EmployeeErrorCode.INVALID_INPUT,
                    message="Target user is not available",
                    status_code=422,
                    details={"field": "user_id"},
                ) from exc
            resolved_user_id = user.id
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
            user_id=resolved_user_id,
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
            details={"employee_no": employee.employee_no}
            if employee.user_id is None
            else {"employee_no": employee.employee_no, "user_id": employee.user_id},
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
        add_duty_ids: tuple[int, ...] | None,
        add_skill_ids: tuple[int, ...] | None,
        clear_department: bool = False,
    ) -> Employee:
        current = self._reload_actor(actor)
        employee = self._repository.get_employee_for_update(employee_id)
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
        details: dict[str, object] = {}
        if status is not None:
            try:
                next_status = EmployeeStatus(status).value
            except ValueError as exc:
                raise AppError(
                    code=EmployeeErrorCode.INVALID_INPUT,
                    message="status is invalid",
                    status_code=422,
                    details={"field": "status"},
                ) from exc
            if employee.status != next_status:
                employee.status = next_status
                details["status"] = employee.status
                if next_status == EmployeeStatus.ACTIVE.value and employee.user_id is not None:
                    from app.service.employee_identity import grant_claimed_staff_property
                    for appointment in self._repository.list_assignments_for_employee(employee.id, lock=True):
                        grant_claimed_staff_property(
                            self._session, employee=employee, assignment=appointment,
                            actor_user_id=current.user_id, request_id=request_id,
                        )
        if clear_department and department_id is not None:
            raise AppError(
                code=EmployeeErrorCode.INVALID_INPUT,
                message="department_id cannot be combined with clear_department",
                status_code=422,
                details={"field": "department_id"},
            )
        if clear_department:
            if employee.department_id is not None:
                employee.department_id = None
                details["department_id"] = None
        elif department_id is not None:
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
            if employee.department_id != department.id:
                employee.department_id = department.id
                details["department_id"] = department.id
        if max_capacity is not None:
            self._validate_capacity(max_capacity)
            if employee.max_capacity != max_capacity:
                employee.max_capacity = max_capacity
                details["max_capacity"] = max_capacity
        added_duty_ids: tuple[int, ...] = ()
        added_skill_ids: tuple[int, ...] = ()
        if add_duty_ids is not None:
            added_duty_ids = self._add_duties(employee, add_duty_ids)
        if add_skill_ids is not None:
            added_skill_ids = self._add_skills(employee, add_skill_ids)
        if added_duty_ids:
            details["added_duty_ids"] = list(added_duty_ids)
        if added_skill_ids:
            details["added_skill_ids"] = list(added_skill_ids)
        if not details:
            return employee
        details.setdefault("status", employee.status)
        self._append_audit(
            action=AuditAction.EMPLOYEE_UPDATED,
            resource_type=AuditResourceType.EMPLOYEE,
            actor_user_id=current.user_id,
            resource_id=employee.id,
            request_id=request_id,
            scope_type=ScopeType.TENANT,
            tenant_id=employee.tenant_id,
            details=details,
        )
        return employee

    def _create_catalog_item(
        self,
        *,
        actor: Principal,
        tenant_id: int,
        property_id: int,
        code: str,
        name: str,
        request_id: str,
        kind: str,
    ) -> Duty | Skill:
        current = self._reload_actor(actor)
        property_record = self._require_managed_property(
            current,
            tenant_id=tenant_id,
            property_id=property_id,
            permission=EMPLOYEE_MANAGE,
        )
        if kind == "DUTY":
            record: Duty | Skill = Duty(
                tenant_id=property_record.tenant_id,
                code=code,
                name=name,
                status="ACTIVE",
            )
            action = AuditAction.EMPLOYEE_DUTY_CREATED
            resource_type = AuditResourceType.DUTY
            add = self._repository.add_duty
            expected = _DUTY_CODE_UNIQUE
        else:
            record = Skill(
                tenant_id=property_record.tenant_id,
                code=code,
                name=name,
                status="ACTIVE",
            )
            action = AuditAction.EMPLOYEE_SKILL_CREATED
            resource_type = AuditResourceType.SKILL
            add = self._repository.add_skill
            expected = _SKILL_CODE_UNIQUE
        try:
            add(record)
        except IntegrityError as exc:
            self._map_insert_error(exc, expected=expected)
        self._append_audit(
            action=action,
            resource_type=resource_type,
            actor_user_id=current.user_id,
            resource_id=record.id,
            request_id=request_id,
            scope_type=ScopeType.TENANT,
            tenant_id=record.tenant_id,
            details={"code": record.code, "name": record.name},
        )
        return record

    def _assign_property(
        self,
        *,
        actor: Principal,
        employee_id: int,
        property_id: int,
        request_id: str,
    ) -> EmployeeProperty:
        current = self._reload_actor(actor)
        employee = self._repository.get_employee_for_update(employee_id)
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
        from app.service.employee_identity import grant_claimed_staff_property
        grant_claimed_staff_property(self._session, employee=employee, assignment=assignment,
                                    actor_user_id=current.user_id, request_id=request_id)
        return assignment

    def _end_assignment(
        self,
        *,
        actor: Principal,
        assignment_id: int,
        request_id: str,
    ) -> EmployeeProperty:
        current = self._reload_actor(actor)
        assignment = self._repository.get_assignment(assignment_id)
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
        # Same mutex/order as claim, appointment creation and auto dispatch.
        employee = self._repository.get_employee_for_update(assignment.employee_id)
        assignment = self._repository.get_assignment_for_update(assignment_id)
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
        from app.service.employee_identity import revoke_ended_staff_property
        revoke_ended_staff_property(self._session, employee=employee,
                                    property_id=assignment.property_id,
                                    actor_user_id=current.user_id, request_id=request_id)
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
    ) -> tuple[tuple[int, ...], tuple[int, ...]]:
        added_duty_ids: list[int] = []
        added_skill_ids: list[int] = []
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
            if self._repository.has_employee_duty(
                employee_id=employee.id,
                duty_id=duty.id,
            ):
                continue
            try:
                with self._session.begin_nested():
                    self._repository.add_employee_duty(
                        EmployeeDuty(
                            employee_id=employee.id,
                            tenant_id=employee.tenant_id,
                            duty_id=duty.id,
                        )
                    )
                added_duty_ids.append(duty.id)
            except IntegrityError as exc:
                if not self._is_expected_duplicate(exc, _DUTY_UNIQUE):
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
            if self._repository.has_employee_skill(
                employee_id=employee.id,
                skill_id=skill.id,
            ):
                continue
            try:
                with self._session.begin_nested():
                    self._repository.add_employee_skill(
                        EmployeeSkill(
                            employee_id=employee.id,
                            tenant_id=employee.tenant_id,
                            skill_id=skill.id,
                        )
                    )
                added_skill_ids.append(skill.id)
            except IntegrityError as exc:
                if not self._is_expected_duplicate(exc, _SKILL_UNIQUE):
                    self._map_insert_error(exc, expected=_SKILL_UNIQUE)
        return tuple(added_duty_ids), tuple(added_skill_ids)

    def _add_duties(
        self,
        employee: Employee,
        duty_ids: tuple[int, ...],
    ) -> tuple[int, ...]:
        if len(set(duty_ids)) != len(duty_ids):
            raise AppError(
                code=EmployeeErrorCode.INVALID_INPUT,
                message="duty_ids contains duplicates",
                status_code=422,
                details={"field": "duty_ids"},
            )
        for duty_id in duty_ids:
            self._require_positive_id(duty_id, "duty_id")
            if self._repository.get_duty_in_tenant(
                tenant_id=employee.tenant_id,
                duty_id=duty_id,
            ) is None:
                raise AppError(
                    code=EmployeeErrorCode.DUTY_NOT_FOUND,
                    message="Duty was not found",
                    status_code=404,
                )
        added, _ = self._link_duties_and_skills(
            employee,
            duty_ids=duty_ids,
            skill_ids=(),
        )
        return added

    def _add_skills(
        self,
        employee: Employee,
        skill_ids: tuple[int, ...],
    ) -> tuple[int, ...]:
        if len(set(skill_ids)) != len(skill_ids):
            raise AppError(
                code=EmployeeErrorCode.INVALID_INPUT,
                message="skill_ids contains duplicates",
                status_code=422,
                details={"field": "skill_ids"},
            )
        for skill_id in skill_ids:
            self._require_positive_id(skill_id, "skill_id")
            if self._repository.get_skill_in_tenant(
                tenant_id=employee.tenant_id,
                skill_id=skill_id,
            ) is None:
                raise AppError(
                    code=EmployeeErrorCode.SKILL_NOT_FOUND,
                    message="Skill was not found",
                    status_code=404,
                )
        _, added = self._link_duties_and_skills(
            employee,
            duty_ids=(),
            skill_ids=skill_ids,
        )
        return added

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

    @staticmethod
    def _visible_employee_assignments(
        actor: Principal,
        employee: Employee,
        assignments: tuple[EmployeeProperty, ...],
        *,
        permission: str,
    ) -> tuple[EmployeeProperty, ...]:
        tenant_scope = OrganizationScope(
            ScopeType.TENANT,
            tenant_id=employee.tenant_id,
        )
        if actor.has_permission(permission, tenant_scope):
            return assignments
        return tuple(
            assignment
            for assignment in assignments
            if actor.has_permission(
                permission,
                OrganizationScope(
                    ScopeType.PROPERTY,
                    tenant_id=employee.tenant_id,
                    property_id=assignment.property_id,
                ),
            )
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

    def _execute(self, operation: Callable[[], T], *, composing: bool = False) -> T:
        from app.core.database import run_composing_transaction, run_owned_transaction

        try:
            runner = run_composing_transaction if composing else run_owned_transaction
            return runner(self._session, operation)
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

    def _execute_availability(self, operation: Callable[[], T]) -> T:
        result = self._execute(operation)
        # Only after the owned commit succeeds. Failed enqueue is observable;
        # existing beat discovers the durable pending work without this hint.
        from app.core.config import get_settings
        from app.worker.enqueue import schedule_dispatch_recovery
        schedule_dispatch_recovery(get_settings())
        return result

    @staticmethod
    def _is_expected_duplicate(
        error: IntegrityError,
        expected: frozenset[str],
    ) -> bool:
        arguments = getattr(error.orig, "args", ())
        number = arguments[0] if arguments else None
        message = str(arguments[1]).casefold() if len(arguments) > 1 else ""
        return number == 1062 and any(
            name.casefold() in message for name in expected
        )

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
    def _validate_catalog_item(*, code: str, name: str) -> None:
        normalized_code = code.strip().upper() if isinstance(code, str) else ""
        normalized_name = name.strip() if isinstance(name, str) else ""
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", normalized_code):
            raise AppError(
                code=EmployeeErrorCode.INVALID_INPUT,
                message="code is invalid",
                status_code=422,
                details={"field": "code"},
            )
        if not normalized_name or len(normalized_name) > 200:
            raise AppError(
                code=EmployeeErrorCode.INVALID_INPUT,
                message="name is invalid",
                status_code=422,
                details={"field": "name"},
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
