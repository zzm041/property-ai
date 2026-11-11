"""Dispatch: assignment, re-assignment, accept/reject/start and capacity checks.

S6 moved these methods out of the old single-class module without editing their bodies.
"""
from .context import (
    AppError, AssignmentStatus, AttendanceOnDuty, AuditAction, AuditResourceType,
    DISPATCH_STAFF_ROLES, Employee, EmployeeStatus, FilterReason, Iterable,
    PENDING_DISPATCH_STATUSES, Principal, ProcessReason, RbacService, SupervisorDecision,
    WORK_ORDER_DECIDE, WORK_ORDER_DISPATCH, WORK_ORDER_PROCESS, WorkOrder, WorkOrderActorType,
    WorkOrderAnalysisOutput, WorkOrderAssignment, WorkOrderCategory, WorkOrderErrorCode,
    WorkOrderEvent, WorkOrderProcess, WorkOrderStatus, _employee_scope_denial,
    capability_filter_reason, category_duty, current_business_date, datetime,
    eligibility_denied, employee_is_capacity_available, logger, map_analysis_output,
    ordinary_maintenance, reset_assignment_sla_deadlines, reset_response_sla_deadline
)


class WorkOrderAssignMixin:
    """Method group mixed into `WorkOrderService`."""

    def assign(
        self,
        *,
        actor: Principal,
        work_order_id: int,
        employee_id: int,
        request_id: str,
        reason: str | None = None,
        version: int | None = None,
        claimed_tenant_id: int | None = None,
        claimed_property_id: int | None = None,
    ) -> WorkOrder:
        self._require_positive_id(employee_id, "employee_id")
        self._validate_request_id(request_id)
        return self._execute(
            lambda: self._assign(
                actor=actor,
                work_order_id=work_order_id,
                employee_id=employee_id,
                request_id=request_id,
                reason=reason,
                version=version,
                claimed_tenant_id=claimed_tenant_id,
                claimed_property_id=claimed_property_id,
            )
        )
    def _assign(
        self,
        *,
        actor: Principal,
        work_order_id: int,
        employee_id: int,
        request_id: str,
        reason: str | None,
        version: int | None,
        claimed_tenant_id: int | None,
        claimed_property_id: int | None,
    ) -> WorkOrder:
        current = self._reload_actor(actor)
        record = self._lock_work_order(work_order_id)
        RbacService.require_permission(
            current,
            WORK_ORDER_DISPATCH,
            self._property_scope(record),
        )
        if (
            claimed_tenant_id is not None
            and claimed_tenant_id != record.tenant_id
        ) or (
            claimed_property_id is not None
            and claimed_property_id != record.property_id
        ):
            raise AppError(
                code=WorkOrderErrorCode.SCOPE_MISMATCH,
                message="Declared scope does not match the work order",
                status_code=422,
            )
        self._assert_version(record, version)
        # Share the global assignment mutex with AUTO, even for authorized
        # manual intervention. No business scope is inferred from this lock.
        # PA-134: the tenant predicate is part of the locking read, so an id
        # from another tenant is indistinguishable from an unknown one here.
        employee = self._employees.get_employee_in_tenant(
            tenant_id=record.tenant_id,
            employee_id=employee_id,
            for_update=True,
        )
        if employee is None:
            self._log_assignment_lookup_missed(
                employee_id, tenant_id=record.tenant_id
            )
        # PA-127 F1: these two pre-checks used to raise the same bare 409 as
        # every other denial, so "unknown employee" and "employee belongs to
        # another tenant" were indistinguishable from a skill or roster gate.
        scope_denial = _employee_scope_denial(
            employee, tenant_id=record.tenant_id
        )
        if scope_denial is not None:
            raise scope_denial
        now = self._clock()
        current_status = WorkOrderStatus(record.status)
        is_first = current_status in PENDING_DISPATCH_STATUSES
        if is_first and current_status is WorkOrderStatus.PENDING_ANALYSIS:
            self._apply_transition(
                record,
                event=WorkOrderEvent.ASSIGN,
                to_status=WorkOrderStatus.PENDING_ASSIGNMENT,
                actor=current,
                actor_type=self._actor_type(current, record),
                request_id=request_id,
                reason_code="ANALYZE",
            )
        elif not is_first:
            if current_status not in {
                WorkOrderStatus.ASSIGNED,
                WorkOrderStatus.ACCEPTED,
                WorkOrderStatus.PROCESSING,
            }:
                raise AppError(
                    code=WorkOrderErrorCode.INVALID_TRANSITION,
                    message="Work order cannot be assigned from the current status",
                    status_code=409,
                )
            if reason is None or not reason.strip():
                raise AppError(
                    code=WorkOrderErrorCode.ALREADY_ASSIGNED,
                    message="Work order is already assigned",
                    status_code=409,
                )
            self._end_current_process(record, now=now)
            self._end_active_assignment(
                record,
                now=now,
                status=AssignmentStatus.REASSIGNED,
                reason=reason,
            )
            self._apply_transition(
                record,
                event=WorkOrderEvent.REASSIGN,
                to_status=WorkOrderStatus.PENDING_ASSIGNMENT,
                actor=current,
                actor_type=self._actor_type(current, record),
                request_id=request_id,
                reason_code="REASSIGN",
            )
        else:
            existing = self._repository.get_active_assignment(record.id)
            if existing is not None:
                raise AppError(
                    code=WorkOrderErrorCode.ALREADY_ASSIGNED,
                    message="Work order is already assigned",
                    status_code=409,
                )
        eligibility = self._eligibility_error(record, employee)
        if eligibility is not None:
            raise eligibility
        assignment = self._create_assignment(
            record,
            employee=employee,
            actor=current,
            now=now,
            reason=reason,
        )
        self._apply_transition(
            record,
            event=WorkOrderEvent.ASSIGN,
            to_status=WorkOrderStatus.ASSIGNED,
            actor=current,
            actor_type=self._actor_type(current, record),
            request_id=request_id,
            reason_code="ASSIGNED",
        )
        record.current_assignment_id = assignment.id
        record.assigned_at = now
        record.accepted_at = None
        record.started_at = None
        record.resolved_at = None
        reset_assignment_sla_deadlines(
            self._repository,
            work_order=record,
            now=now,
        )
        self._adjust_load(record, employee, delta=1)
        from app.service.incident import IncidentCapacityService

        IncidentCapacityService(
            self._session,
            work_order_repository=self._repository,
        ).adopt_assignment(
            work_order=record,
            assignment=assignment,
        )
        audit_action = (
            AuditAction.WORK_ORDER_ASSIGNED
            if is_first
            else AuditAction.WORK_ORDER_REASSIGNED
        )
        details: dict[str, object] = {
            "work_order_id": record.id,
            "employee_id": employee.id,
            "status": record.status,
        }
        if not is_first:
            details["reason_code"] = "REASSIGN"
        self._append_audit(
            action=audit_action,
            resource_type=AuditResourceType.WORK_ORDER_ASSIGNMENT,
            actor_user_id=current.user_id,
            resource_id=assignment.id,
            request_id=request_id,
            tenant_id=record.tenant_id,
            property_id=record.property_id,
            details=details,
        )
        from app.service.analysis import AnalysisService

        AnalysisService(self._session, settings=self._settings).mark_assignment_choice(
            work_order=record,
            employee_id=employee.id,
            actor_user_id=current.user_id,
            request_id=request_id,
        )
        self._notify_users(
            record,
            user_ids=(employee.user_id, record.reporter_user_id),
            title="工单已派单",
            body="工单已安排处理，请查看最新进度",
        )
        return record
    def decide(
        self,
        *,
        actor: Principal,
        work_order_id: int,
        decision: str,
        request_id: str,
        employee_id: int | None = None,
        reason: str | None = None,
        version: int | None = None,
    ) -> WorkOrder:
        self._validate_request_id(request_id)
        return self._execute(
            lambda: self._decide(
                actor=actor,
                work_order_id=work_order_id,
                decision=decision,
                request_id=request_id,
                employee_id=employee_id,
                reason=reason,
                version=version,
            )
        )
    def _decide(
        self,
        *,
        actor: Principal,
        work_order_id: int,
        decision: str,
        request_id: str,
        employee_id: int | None,
        reason: str | None,
        version: int | None,
    ) -> WorkOrder:
        current = self._reload_actor(actor)
        record = self._lock_work_order(work_order_id)
        RbacService.require_permission(
            current,
            WORK_ORDER_DECIDE,
            self._property_scope(record),
        )
        self._assert_version(record, version)
        try:
            code = SupervisorDecision(decision)
        except ValueError as exc:
            raise AppError(
                code=WorkOrderErrorCode.INVALID_INPUT,
                message="decision is invalid",
                status_code=422,
            ) from exc
        if record.status != WorkOrderStatus.PROCESSING.value:
            raise AppError(
                code=WorkOrderErrorCode.INVALID_TRANSITION,
                message="Decision is only allowed while processing",
                status_code=409,
            )
        cleaned_reason = self._optional_text(reason)
        if code in {SupervisorDecision.CONTINUE, SupervisorDecision.REASSIGN} and cleaned_reason is None:
            raise AppError(
                code=WorkOrderErrorCode.INVALID_INPUT,
                message=f"reason is required for {code.value}",
                status_code=422,
            )
        active_assignee_user_id = None
        if code is SupervisorDecision.REASSIGN:
            if employee_id is None:
                now = self._clock()
                self._end_current_process(record, now=now)
                self._end_active_assignment(
                    record,
                    now=now,
                    status=AssignmentStatus.REASSIGNED,
                    reason=cleaned_reason,
                )
                self._apply_transition(
                    record,
                    event=WorkOrderEvent.REASSIGN,
                    to_status=WorkOrderStatus.PENDING_ASSIGNMENT,
                    actor=current,
                    actor_type=self._actor_type(current, record),
                    request_id=request_id,
                    reason_code=ProcessReason.REASSIGN_REQUIRED.value,
                )
            else:
                return self._assign(
                    actor=current,
                    work_order_id=record.id,
                    employee_id=employee_id,
                    request_id=request_id,
                    reason=cleaned_reason,
                    version=record.version,
                    claimed_tenant_id=None,
                    claimed_property_id=None,
                )
        else:
            active_assignee_user_id = self._repository.get_active_assignee_user_id(
                record.id
            )
            reason_map = {
                SupervisorDecision.WAIT_PARTS: ProcessReason.WAITING_PARTS,
                SupervisorDecision.WAIT_OWNER: ProcessReason.WAITING_OWNER,
                SupervisorDecision.EXTERNAL_COORDINATION: (
                    ProcessReason.EXTERNAL_COORDINATION
                ),
                SupervisorDecision.CONTINUE: ProcessReason.NONE,
            }
            target_reason = reason_map[code]
            self._apply_transition(
                record,
                event=WorkOrderEvent.DECIDE,
                to_status=WorkOrderStatus.PROCESSING,
                actor=current,
                actor_type=self._actor_type(current, record),
                request_id=request_id,
                process_reason=target_reason,
                reason_code=code.value,
                reason_detail=cleaned_reason,
            )
            if code is SupervisorDecision.CONTINUE:
                self._resume_receipted_process(record, now=self._clock())
        self._append_audit(
            action=AuditAction.WORK_ORDER_DECIDED,
            resource_type=AuditResourceType.WORK_ORDER,
            actor_user_id=current.user_id,
            resource_id=record.id,
            request_id=request_id,
            tenant_id=record.tenant_id,
            property_id=record.property_id,
            details={
                "work_order_id": record.id,
                "status": record.status,
                "process_reason": record.process_reason,
                "reason_code": code.value,
            },
        )
        if code is SupervisorDecision.REASSIGN:
            recipients: Iterable[int | None] = (record.reporter_user_id,)
            title = "工单待重新安排"
            body = "主管已将工单转回待派单队列"
        else:
            recipients = (record.reporter_user_id, active_assignee_user_id)
            title = "工单处理方案已更新"
            body = "主管已更新工单处理方案，请查看详情"
        self._notify_users(
            record,
            user_ids=recipients,
            exclude_user_ids=(current.user_id,),
            title=title,
            body=body,
        )
        return record
    def accept(
        self,
        *,
        actor: Principal,
        work_order_id: int,
        request_id: str,
        version: int | None = None,
    ) -> WorkOrder:
        self._validate_request_id(request_id)
        return self._execute(
            lambda: self._accept(
                actor=actor,
                work_order_id=work_order_id,
                request_id=request_id,
                version=version,
            )
        )
    def _accept(
        self,
        *,
        actor: Principal,
        work_order_id: int,
        request_id: str,
        version: int | None,
    ) -> WorkOrder:
        current, record, assignment, employee = self._lock_assignee_action(
            actor, work_order_id, version, takeover=False
        )
        now = self._clock()
        assignment.accept_at = now
        self._apply_transition(
            record,
            event=WorkOrderEvent.ACCEPT,
            to_status=WorkOrderStatus.ACCEPTED,
            actor=current,
            actor_type=WorkOrderActorType.STAFF,
            request_id=request_id,
            reason_code="ACCEPTED",
        )
        record.accepted_at = now
        reset_response_sla_deadline(
            self._repository,
            work_order=record,
            now=now,
        )
        self._append_audit(
            action=AuditAction.WORK_ORDER_ACCEPTED,
            resource_type=AuditResourceType.WORK_ORDER,
            actor_user_id=current.user_id,
            resource_id=record.id,
            request_id=request_id,
            tenant_id=record.tenant_id,
            property_id=record.property_id,
            details={
                "work_order_id": record.id,
                "employee_id": employee.id,
                "status": record.status,
            },
        )
        self._notify_users(
            record,
            user_ids=(record.reporter_user_id,),
            title="工单已接单",
            body="工作人员已接单，请留意后续进度",
        )
        return record
    def reject(
        self,
        *,
        actor: Principal,
        work_order_id: int,
        request_id: str,
        reason: str,
        version: int | None = None,
    ) -> WorkOrder:
        self._validate_request_id(request_id)
        if not isinstance(reason, str) or not reason.strip():
            raise AppError(
                code=WorkOrderErrorCode.INVALID_INPUT,
                message="reason is required",
                status_code=422,
            )
        record = self._execute(
            lambda: self._reject(
                actor=actor,
                work_order_id=work_order_id,
                request_id=request_id,
                reason=reason.strip(),
                version=version,
            ),
            analysis_request_id=request_id,
        )
        self._enqueue_analysis(record.id, request_id, version=record.version)
        return record
    def _reject(
        self,
        *,
        actor: Principal,
        work_order_id: int,
        request_id: str,
        reason: str,
        version: int | None,
    ) -> WorkOrder:
        current, record, assignment, employee = self._lock_assignee_action(
            actor, work_order_id, version, takeover=False
        )
        now = self._clock()
        assignment.reject_reason = reason
        self._end_active_assignment(
            record,
            now=now,
            status=AssignmentStatus.REJECTED,
            reason=reason,
        )
        self._apply_transition(
            record,
            event=WorkOrderEvent.REJECT,
            to_status=WorkOrderStatus.PENDING_ASSIGNMENT,
            actor=current,
            actor_type=WorkOrderActorType.STAFF,
            request_id=request_id,
            reason_code="REJECTED",
        )
        record.current_assignment_id = None
        self._append_audit(
            action=AuditAction.WORK_ORDER_REJECTED,
            resource_type=AuditResourceType.WORK_ORDER,
            actor_user_id=current.user_id,
            resource_id=record.id,
            request_id=request_id,
            tenant_id=record.tenant_id,
            property_id=record.property_id,
            details={
                "work_order_id": record.id,
                "employee_id": employee.id,
                "status": record.status,
                "reason_code": "REJECTED",
            },
        )
        self._notify_users(
            record,
            user_ids=(record.reporter_user_id,),
            title="工单待重新安排",
            body="原处理人员无法接单，系统正在重新安排",
        )
        return record
    def start(
        self,
        *,
        actor: Principal,
        work_order_id: int,
        request_id: str,
        version: int | None = None,
    ) -> WorkOrder:
        self._validate_request_id(request_id)
        return self._execute(
            lambda: self._start(
                actor=actor,
                work_order_id=work_order_id,
                request_id=request_id,
                version=version,
            )
        )
    def _start(
        self,
        *,
        actor: Principal,
        work_order_id: int,
        request_id: str,
        version: int | None,
    ) -> WorkOrder:
        current, record, _assignment, employee = self._lock_assignee_action(
            actor, work_order_id, version, takeover=True
        )
        now = self._clock()
        self._apply_transition(
            record,
            event=WorkOrderEvent.START,
            to_status=WorkOrderStatus.PROCESSING,
            actor=current,
            actor_type=self._actor_type(current, record),
            request_id=request_id,
            reason_code="STARTED",
        )
        record.started_at = now
        existing = self._repository.get_current_process(record.id)
        cycle_no = 1 if existing is None else existing.cycle_no + 1
        if existing is None or existing.ended_at is not None:
            self._repository.add_process(
                WorkOrderProcess(
                    tenant_id=record.tenant_id,
                    property_id=record.property_id,
                    work_order_id=record.id,
                    cycle_no=cycle_no if existing is not None else 1,
                    assignee_id=employee.id,
                    started_at=now,
                )
            )
        self._append_audit(
            action=AuditAction.WORK_ORDER_PROCESS_STARTED,
            resource_type=AuditResourceType.WORK_ORDER,
            actor_user_id=current.user_id,
            resource_id=record.id,
            request_id=request_id,
            tenant_id=record.tenant_id,
            property_id=record.property_id,
            details={"work_order_id": record.id, "status": record.status},
        )
        self._notify_users(
            record,
            user_ids=(record.reporter_user_id,),
            title="工单开始处理",
            body="工作人员已开始处理工单",
        )
        return record
    def _lock_assignee_action(
        self,
        actor: Principal,
        work_order_id: int,
        version: int | None,
        *,
        takeover: bool,
    ) -> tuple[Principal, WorkOrder, WorkOrderAssignment, Employee]:
        current = self._reload_actor(actor)
        record = self._lock_work_order(work_order_id)
        RbacService.require_permission(
            current,
            WORK_ORDER_PROCESS,
            self._property_scope(record),
        )
        assignment = self._repository.get_active_assignment_for_update(record.id)
        if assignment is None:
            raise AppError(
                code=WorkOrderErrorCode.NOT_ASSIGNEE,
                message="Work order has no active assignee",
                status_code=403,
            )
        employee = self._require_actor_employee(current)
        can_takeover = current.has_permission(
            WORK_ORDER_DISPATCH,
            self._property_scope(record),
        ) or current.has_permission(
            WORK_ORDER_DECIDE,
            self._property_scope(record),
        )
        if employee.id != assignment.employee_id and not (takeover and can_takeover):
            raise AppError(
                code=WorkOrderErrorCode.NOT_ASSIGNEE,
                message="Caller is not the current assignee",
                status_code=403,
            )
        if employee.id != assignment.employee_id:
            assigned = self._employees.get_employee(assignment.employee_id)
            if assigned is None:
                raise AppError(
                    code=WorkOrderErrorCode.NOT_ASSIGNEE,
                    message="Caller is not the current assignee",
                    status_code=403,
                )
            employee = assigned
        self._assert_version(record, version)
        if record.status in {
            WorkOrderStatus.CLOSED.value,
            WorkOrderStatus.CANCELLED.value,
            WorkOrderStatus.COMPLETED.value,
        }:
            raise AppError(
                code=WorkOrderErrorCode.ALREADY_PROCESSED,
                message="Work order is already finished",
                status_code=409,
            )
        return current, record, assignment, employee
    def _create_assignment(
        self,
        record: WorkOrder,
        *,
        employee: Employee,
        actor: Principal,
        now: datetime,
        reason: str | None,
    ) -> WorkOrderAssignment:
        return self._repository.add_assignment(
            WorkOrderAssignment(
                tenant_id=record.tenant_id,
                property_id=record.property_id,
                work_order_id=record.id,
                employee_id=employee.id,
                assigned_by_user_id=actor.user_id,
                status=AssignmentStatus.ACTIVE.value,
                assigned_at=now,
                reason=reason,
            )
        )
    def _end_active_assignment(
        self,
        record: WorkOrder,
        *,
        now: datetime,
        status: AssignmentStatus,
        reason: str | None = None,
    ) -> None:
        assignment = self._repository.get_active_assignment_for_update(record.id)
        if assignment is None:
            return
        assignment.status = status.value
        assignment.ended_at = now
        if reason is not None:
            assignment.reason = reason
        employee = self._employees.get_employee(assignment.employee_id)
        from app.service.incident import IncidentCapacityService

        release_load = IncidentCapacityService(
            self._session,
            work_order_repository=self._repository,
        ).should_release_load(
            work_order=record,
            assignment=assignment,
        )
        if employee is not None and release_load:
            self._adjust_load(record, employee, delta=-1)
        record.current_assignment_id = None
        self._session.flush()
    def _adjust_load(
        self,
        record: WorkOrder,
        employee: Employee,
        *,
        delta: int,
    ) -> None:
        property_record = self._repository.get_property(record.property_id)
        if property_record is None:
            return
        business_date = current_business_date(
            property_record.timezone,
            clock=self._clock,
        )
        daily = self._employees.get_daily_status_for_update(
            employee_id=employee.id,
            property_id=record.property_id,
            business_date=business_date,
        )
        if daily is None:
            return
        daily.current_load = max(daily.current_load + delta, 0)
        self._session.flush()
    def _manual_requirements(self, record: WorkOrder) -> tuple[str | None, str | None] | None:
        category = WorkOrderCategory(getattr(record, "category", "OTHER"))
        required_duty = None if category is WorkOrderCategory.OTHER else category_duty(category)
        required_skill = None if category is WorkOrderCategory.OTHER else category.value
        analysis = self._analysis.get_current_analysis(record.id)
        if analysis is not None and analysis.status == "COMPLETED":
            try:
                output = WorkOrderAnalysisOutput.model_validate({
                    field: getattr(analysis, field, None)
                    for field in WorkOrderAnalysisOutput.model_fields
                })
                mapped = map_analysis_output(output, threshold=self._settings.ai_confidence_threshold)
            except ValueError:
                # A purportedly completed but malformed requirement must not
                # silently discard a restriction and permit assignment.
                return None
            if mapped.category == category and not mapped.low_confidence:
                required_duty, required_skill = mapped.required_duty, mapped.required_skill
        return required_duty, required_skill
    def _eligibility_error(
        self,
        record: WorkOrder,
        employee: Employee,
        *,
        ignore_capacity: bool = False,
    ) -> AppError | None:
        # PA-101-A: one shared 409 body used to hide every denial cause, so a
        # rejected manual dispatch left the operator with no way to tell a skill
        # gate from attendance or capacity. The code and status stay the same;
        # only the machine-readable reason is now filled in.
        required_duty: str | None = None
        required_skill: str | None = None

        def denied(reason: str) -> AppError:
            return eligibility_denied(
                reason=reason,
                required_duty=required_duty,
                required_skill=required_skill,
            )

        if employee.status != EmployeeStatus.ACTIVE.value:
            return denied(FilterReason.NOT_ACTIVE.value)
        # PA-127 F1: `_assign` rejects a foreign employee before calling here,
        # so this branch is only reachable from callers that load the assignee
        # themselves (the resident reopen path at :1776). Kept as defence in
        # depth; the manual path now reports TENANT_MISMATCH from the
        # pre-check instead.
        if employee.tenant_id != record.tenant_id:
            return denied("TENANT_MISMATCH")
        if employee.user_id is None or self._analysis.get_user_status(employee.user_id) != "ACTIVE":
            return denied(FilterReason.USER_NOT_ACTIVE.value)
        roles = self._analysis.list_employee_role_codes(
            employee.user_id, tenant_id=record.tenant_id, property_id=record.property_id,
        )
        if not roles & DISPATCH_STAFF_ROLES:
            return denied(FilterReason.NO_STAFF_ROLE.value)
        now = self._clock()
        assignment = self._employees.find_effective_assignment(
            employee_id=employee.id,
            property_id=record.property_id,
            now=now,
        )
        if assignment is None:
            return denied(FilterReason.PROPERTY_NOT_EFFECTIVE.value)
        property_record = self._repository.get_property(record.property_id)
        if property_record is None:
            return denied(FilterReason.PROPERTY_NOT_EFFECTIVE.value)
        business_date = current_business_date(
            property_record.timezone,
            clock=self._clock,
        )
        daily = self._employees.get_daily_status_for_update(
            employee_id=employee.id,
            property_id=record.property_id,
            business_date=business_date,
        )
        if daily is None or daily.on_duty != AttendanceOnDuty.ON_DUTY.value:
            return denied(FilterReason.NOT_ON_DUTY.value)
        attendance = self._analysis.get_attendance(
            employee_id=employee.id,
            property_id=record.property_id,
            business_date=business_date,
        )
        if attendance is None or attendance.check_in_at is None:
            return denied(FilterReason.NOT_CHECKED_IN.value)
        requirements = self._manual_requirements(record)
        if requirements is None:
            return denied("ANALYSIS_UNREADABLE")
        required_duty, required_skill = requirements
        # Unknown classification keeps the explicitly retained manual fallback.
        if required_duty is not None:
            capability = capability_filter_reason(
                duty_codes=self._analysis.list_employee_duties(employee.id),
                skill_codes=self._analysis.list_employee_skills(employee.id),
                required_duty=required_duty, required_skill=required_skill,
            )
            if capability is not None:
                return denied(capability.value)
        if not ignore_capacity:
            load = self._repository.employee_global_occupancy(
                employee_id=employee.id, tenant_id=record.tenant_id, lock=True, collapse_incidents=True,
            )
            if not employee_is_capacity_available(current_load=load, max_capacity=employee.max_capacity):
                return denied(FilterReason.CAPACITY_FULL.value)
            if ordinary_maintenance(required_duty, required_skill) and load > 0:
                return denied(FilterReason.BUSY.value)
        return None
    def _log_assignment_lookup_missed(
        self, employee_id: int, *, tenant_id: int
    ) -> None:
        """Record *why* the tenant-scoped assignee lookup came back empty.

        PA-134: the 409 body is deliberately identical for "no such id" and
        "id belongs to another tenant", so the reason is kept for internal
        diagnostics only and never leaves the process. The extra read runs on
        the denial path only.
        """

        foreign = self._employees.get_employee(employee_id) is not None
        logger.warning(
            "dispatch_employee_lookup_missed",
            employee_id=employee_id,
            tenant_id=tenant_id,
            error_reason="EMPLOYEE_FOREIGN" if foreign else "EMPLOYEE_NOT_FOUND",
        )
    def _require_actor_employee(self, actor: Principal) -> Employee:
        employee = self._employees.get_employee_for_user(actor.user_id)
        if employee is None:
            raise AppError(
                code=WorkOrderErrorCode.NOT_ASSIGNEE,
                message="Caller is not the current assignee",
                status_code=403,
            )
        return employee
