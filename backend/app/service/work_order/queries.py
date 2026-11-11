"""Read paths: detail, queues, diagnosis, visibility and pagination.

S6 moved these methods out of the old single-class module without editing their bodies.
"""
from .context import (
    AiErrorCode, AppError, AuditAction, AuditResourceType, EmployeeStatus, FileErrorCode,
    FileObject, IamErrorCode, Iterable, OrganizationScope, PENDING_DISPATCH_STATUSES,
    Principal, RbacService, ScopeType, WORK_ORDER_DECIDE, WORK_ORDER_DISPATCH, WORK_ORDER_READ,
    WorkOrder, WorkOrderActorType, WorkOrderAssignment, WorkOrderContent, WorkOrderErrorCode,
    WorkOrderEvent, WorkOrderFeedback, WorkOrderLog, WorkOrderMedia, WorkOrderProcess,
    WorkOrderReceipt, WorkOrderStatus, _ESCALATION_REASONS, _QUEUE_STATUSES, datetime,
    is_legal_transition, resident_media_visible, sla_is_overdue
)


class WorkOrderQueriesMixin:
    """Method group mixed into `WorkOrderService`."""

    def get_work_order(self, *, actor: Principal, work_order_id: int) -> WorkOrder:
        self._require_positive_id(work_order_id, "work_order_id")
        current = self._reload_actor(actor)
        record = self._repository.get_work_order(work_order_id)
        if record is None:
            self._raise_not_found()
        self._authorize_read(current, record)
        return record
    def get_work_order_detail(
        self,
        *,
        actor: Principal,
        work_order_id: int,
        audience: str = "resident",
    ) -> tuple[
        WorkOrder,
        WorkOrderContent | None,
        tuple[WorkOrderMedia, ...],
        tuple[WorkOrderAssignment, ...],
        tuple[WorkOrderProcess, ...],
        tuple[WorkOrderReceipt, ...],
        tuple[WorkOrderFeedback, ...],
        tuple[WorkOrderLog, ...],
        dict[str, object],
    ]:
        self._validate_audience(audience)
        record = self.get_work_order(actor=actor, work_order_id=work_order_id)
        if audience == "property":
            self._authorize_read(self._reload_actor(actor), record, allow_reporter=False)
        analysis = self._analysis.get_current_analysis(record.id)
        display_context = self._repository.get_display_context(record)
        display_context["analysis_status"] = None if analysis is None else analysis.status
        if (analysis is not None and analysis.status == "LOW_CONFIDENCE"
                and getattr(analysis, "error_code", None) == AiErrorCode.OUT_OF_SCOPE):
            display_context["analysis_disposition"] = "OUT_OF_SCOPE_REQUEST"
        return (
            record,
            self._repository.get_content(record.id),
            self._repository.list_media(record.id),
            self._repository.list_assignments(record.id),
            self._repository.list_processes(record.id),
            self._repository.list_receipts(record.id),
            self._repository.list_feedbacks(record.id),
            self._repository.list_logs(record.id),
            display_context,
        )
    def list_my_work_orders(
        self,
        *,
        actor: Principal,
        after_id: int = 0,
        limit: int = 50,
    ) -> tuple[WorkOrder, ...]:
        current = self._reload_actor(actor)
        page_limit = self._page_limit(limit)
        return self._repository.list_for_reporter(
            user_id=current.user_id,
            after_id=after_id,
            # The resident endpoint consumes one look-ahead record so it can
            # distinguish an exact final page from a page that has more data.
            limit=page_limit + 1,
        )
    def list_supervisor_queue(
        self,
        *,
        actor: Principal,
        property_id: int,
        after_id: int = 0,
        limit: int = 50,
        claimed_tenant_id: int | None = None,
        view: str = "dispatch",
    ) -> tuple[WorkOrder, ...]:
        self._require_positive_id(property_id, "property_id")
        current = self._reload_actor(actor)
        property_record = self._repository.get_property(property_id)
        if property_record is None:
            raise AppError(
                code=WorkOrderErrorCode.NOT_FOUND,
                message="Work order was not found",
                status_code=404,
            )
        if (
            claimed_tenant_id is not None
            and claimed_tenant_id != property_record.tenant_id
        ):
            raise AppError(
                code=WorkOrderErrorCode.SCOPE_MISMATCH,
                message="Declared tenant does not match the target property",
                status_code=422,
            )
        scope = OrganizationScope(
            ScopeType.PROPERTY,
            tenant_id=property_record.tenant_id,
            property_id=property_record.id,
        )
        RbacService.require_permission(current, WORK_ORDER_DISPATCH, scope)
        if view not in {"dispatch", "unstarted"}:
            raise AppError(
                code=WorkOrderErrorCode.INVALID_INPUT,
                message="Unknown work-order queue view",
                status_code=422,
            )
        # Derive the management list from the same frozen FSM used by cancel.
        statuses = _QUEUE_STATUSES if view == "dispatch" else tuple(
            status.value for status in WorkOrderStatus
            if is_legal_transition(
                from_status=status,
                to_status=WorkOrderStatus.CANCELLED,
                event=WorkOrderEvent.CANCEL,
            )
        )
        records = self._repository.list_queue_for_property(
            tenant_id=property_record.tenant_id,
            property_id=property_record.id,
            statuses=statuses,
            after_id=after_id,
            limit=self._page_limit(limit),
        )
        if view == "unstarted":
            return records
        return tuple(
            item
            for item in records
            if item.status in {status.value for status in PENDING_DISPATCH_STATUSES}
            or item.process_reason in _ESCALATION_REASONS
        )
    def list_staff_work_orders(
        self,
        *,
        actor: Principal,
        after_id: int = 0,
        limit: int = 50,
    ) -> tuple[WorkOrder, ...]:
        current = self._reload_actor(actor)
        employee = self._require_actor_employee(current)
        if employee.status != EmployeeStatus.ACTIVE.value:
            raise AppError(code=IamErrorCode.FORBIDDEN, message="Permission denied", status_code=403)
        property_ids = tuple(
            property_id
            for property_id in self._employees.list_active_property_ids_for_employee(
                employee_id=employee.id, now=self._clock(),
            )
            if current.has_permission(
                WORK_ORDER_READ,
                OrganizationScope(ScopeType.PROPERTY, tenant_id=employee.tenant_id, property_id=property_id),
            )
        )
        if not property_ids:
            return ()
        return self._repository.list_for_assignee(
            employee_id=employee.id,
            tenant_id=employee.tenant_id,
            property_ids=property_ids,
            after_id=after_id,
            limit=self._page_limit(limit),
        )
    def diagnose_work_order(self, *, actor: Principal, work_order_id: int,
                            property_id: int, version: int):
        """Read existing evidence only. No inference, mutation, or scheduling."""
        from app.domain.diagnosis import diagnosis_items
        from app.schema.work_order import WorkOrderDiagnosisData

        self._require_positive_id(work_order_id, 'work_order_id')
        self._require_positive_id(property_id, 'property_id')
        self._require_positive_id(version, 'version')
        current = self._reload_actor(actor)
        record = self._repository.get_work_order(work_order_id)
        if record is None:
            self._raise_not_found()
        scope = OrganizationScope(ScopeType.PROPERTY, record.tenant_id, record.property_id)
        RbacService.require_permission(current, WORK_ORDER_DISPATCH, scope)
        if record.property_id != property_id:
            raise AppError(code=WorkOrderErrorCode.SCOPE_MISMATCH, message='Work order scope changed', status_code=422)
        self._assert_version(record, version)
        observed_at = self._clock()
        analysis = self._analysis.get_current_analysis(record.id)
        process = self._repository.get_current_process(record.id)
        wait = self._repository.latest_dispatch_wait(work_order_id=record.id,
            tenant_id=record.tenant_id, property_id=record.property_id)
        payload = None if wait is None else wait.payload_json
        wait_reason = payload.get('reason_code') if isinstance(payload, dict) else None
        if not isinstance(wait_reason, str):
            wait_reason = None
        facts, actions = diagnosis_items(status=record.status, process_reason=record.process_reason,
            analysis_status=None if analysis is None else analysis.status,
            analysis_error_code=None if analysis is None else getattr(analysis, 'error_code', None),
            cycle_no=None if process is None else process.cycle_no,
            wait_reason=wait_reason, overdue=self.is_overdue(record, observed_at))
        sources = {'work_order': record, 'analysis': analysis,
                   'work_order_log': wait, 'work_order_process': process}
        return WorkOrderDiagnosisData(work_order_id=record.id, property_id=record.property_id,
            version=record.version, observed_at=observed_at,
            facts=[dict(code=code, text=text, source_type=kind, source_id=sources[kind].id,
                        recorded_at=getattr(sources[kind], 'updated_at', None) if kind == 'work_order'
                        else getattr(sources[kind], 'created_at', None)) for code, text, kind in facts],
            suggested_actions=actions)
    def get_contact(self, *, actor: Principal, work_order_id: int, reveal: bool, request_id: str) -> dict:
        """Explicit, scoped plaintext access; never returned by common list DTOs."""
        from app.domain.phone import mask_phone

        self._require_positive_id(work_order_id, "work_order_id")
        self._validate_request_id(request_id)
        def operation():
            current = self._reload_actor(actor)
            record = self._lock_work_order(work_order_id)
            scope = self._property_scope(record)
            scoped = Principal(current.user_id, current.status, current.display_name,
                               tuple(g for g in current.grants if g.scope.scope_type is not ScopeType.SYSTEM))
            allowed = record.reporter_user_id == current.user_id
            if not allowed:
                management = scoped.has_permission(WORK_ORDER_READ, scope) and (
                    scoped.has_permission(WORK_ORDER_DISPATCH, scope) or scoped.has_permission(WORK_ORDER_DECIDE, scope))
                employee = self._employees.get_employee_for_user(current.user_id)
                assignment = self._repository.get_active_assignment(record.id)
                responsible = (
                    scoped.has_permission(WORK_ORDER_READ, scope) and employee is not None
                    and employee.status == EmployeeStatus.ACTIVE.value
                    and employee.tenant_id == record.tenant_id and assignment is not None
                    and assignment.employee_id == employee.id
                    and self._employees.find_effective_assignment(
                        employee_id=employee.id, property_id=record.property_id, now=self._clock()) is not None
                )
                allowed = management or responsible
            if not allowed:
                raise AppError(code=IamErrorCode.FORBIDDEN, message="Contact access denied", status_code=403)
            phone = record.contact_phone_e164
            if reveal and phone:
                self._append_audit(action=AuditAction.WORK_ORDER_CONTACT_READ,
                    resource_type=AuditResourceType.WORK_ORDER, actor_user_id=current.user_id,
                    resource_id=record.id, request_id=request_id, scope_type=ScopeType.PROPERTY,
                    tenant_id=record.tenant_id, property_id=record.property_id,
                    details={"work_order_id": record.id})
            return {"collected": bool(phone), "masked_phone": mask_phone(phone) if phone else None,
                    "phone": phone if reveal else None}
        return self._execute(operation)
    def get_file_content(
        self,
        *,
        actor: Principal,
        file_id: int,
        audience: str = "resident",
    ) -> tuple[FileObject, bytes]:
        current = self._reload_actor(actor)
        self._require_positive_id(file_id, "file_id")
        record = self._repository.get_file(file_id)
        if record is None:
            self._raise_file_not_found()
        self._validate_audience(audience)
        if not self._file_is_visible(current, record, audience=audience):
            self._raise_file_not_found()
        return record, self._read_stored_file(record)
    def _file_is_visible(self, current: Principal, record: FileObject, *, audience: str) -> bool:
        """Metadata-only gate shared by downloads and new media associations."""
        orders = self._repository.list_work_orders_for_file(record.id)
        allowed = not orders and record.uploader_user_id == current.user_id
        for work_order in orders:
            try:
                self._authorize_read(current, work_order, allow_reporter=audience == "resident")
            except AppError:
                continue
            if audience == "property":
                allowed = True
            else:
                receipt_ids = {item.id for item in self._repository.list_receipts(work_order.id)}
                allowed = any(
                    item.file_id == record.id and resident_media_visible(
                        phase=item.taken_phase, receipt_id=item.receipt_id, receipt_ids=receipt_ids,
                    )
                    for item in self._repository.list_media(work_order.id)
                )
            if allowed:
                break
        return bool(allowed)
    def is_overdue(self, record: WorkOrder, now: datetime | None = None) -> bool:
        return sla_is_overdue(due_at=record.sla_due_at, now=now or self._clock())
    def quarantine_notes(self, work_order_ids: Iterable[int]) -> dict[int, str]:
        """Explain, per work order, why its analysis never ran (PA-169/t152 F4)."""

        return self._repository.quarantine_notes(tuple(work_order_ids))
    def _authorize_read(self, actor: Principal, record: WorkOrder, *, allow_reporter: bool = True) -> None:
        if allow_reporter and record.reporter_user_id == actor.user_id:
            return
        scope = self._property_scope(record)
        if actor.has_permission(WORK_ORDER_DISPATCH, scope) or actor.has_permission(
            WORK_ORDER_DECIDE, scope
        ):
            RbacService.require_permission(actor, WORK_ORDER_READ, scope)
            return
        if actor.has_permission(WORK_ORDER_READ, scope):
            assignment = self._repository.get_active_assignment(record.id)
            employee = self._employees.get_employee_for_user(actor.user_id)
            if (
                assignment is not None
                and employee is not None
                and assignment.employee_id == employee.id
                and employee.status == EmployeeStatus.ACTIVE.value
                and employee.tenant_id == record.tenant_id
                and self._employees.find_effective_assignment(
                    employee_id=employee.id,
                    property_id=record.property_id,
                    now=self._clock(),
                ) is not None
            ):
                return
        raise AppError(
            code=IamErrorCode.FORBIDDEN,
            message="Permission denied",
            status_code=403,
        )
    @staticmethod
    def _validate_audience(audience: str) -> None:
        if audience not in {"resident", "property"}:
            raise AppError(code=WorkOrderErrorCode.INVALID_INPUT, message="Invalid audience", status_code=422)
    def _require_reporter(self, actor: Principal, record: WorkOrder) -> None:
        if record.reporter_user_id != actor.user_id:
            raise AppError(
                code=IamErrorCode.FORBIDDEN,
                message="Permission denied",
                status_code=403,
            )
        RbacService.require_permission(
            actor,
            WORK_ORDER_READ,
            self._property_scope(record),
        )
    def _property_scope(self, record: WorkOrder) -> OrganizationScope:
        return OrganizationScope(
            ScopeType.PROPERTY,
            tenant_id=record.tenant_id,
            property_id=record.property_id,
        )
    def _actor_can_see_file_tenant(self, actor: Principal, tenant_id: int) -> bool:
        for grant in actor.grants:
            if grant.scope.scope_type == ScopeType.SYSTEM:
                return True
            if grant.scope.tenant_id == tenant_id:
                return True
        return False
    @staticmethod
    def _page_limit(limit: int) -> int:
        if limit < 1:
            return 1
        return min(limit, 100)
    @staticmethod
    def _raise_not_found() -> None:
        raise AppError(
            code=WorkOrderErrorCode.NOT_FOUND,
            message="Work order was not found",
            status_code=404,
        )
    @staticmethod
    def _raise_file_not_found() -> None:
        raise AppError(
            code=FileErrorCode.NOT_FOUND,
            message="File was not found",
            status_code=404,
        )
    def _reload_actor(self, actor: Principal) -> Principal:
        self._require_active_actor(actor)
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
    def _actor_type(
        self,
        actor: Principal,
        record: WorkOrder,
    ) -> WorkOrderActorType:
        if record.reporter_user_id == actor.user_id:
            return WorkOrderActorType.RESIDENT
        scope = self._property_scope(record)
        if actor.has_permission(WORK_ORDER_DECIDE, scope):
            return WorkOrderActorType.SUPERVISOR
        return WorkOrderActorType.STAFF
