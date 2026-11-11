"""Work order creation, the status machine, completion and the shared plumbing.

S6 moved these methods out of the old single-class module without editing their bodies.
"""
from .context import (
    AiErrorCode, AiRepository, AppError, AssignmentStatus, AuditAction, AuditResourceType,
    AuditService, AuthErrorCode, AuthService, Callable, DUPLICATE_WINDOW_MINUTES,
    EmployeeRepository, ErrorCode, FileScannerProvider, IamErrorCode, IdempotencyRecord,
    IdentityRepository, IntegrityError, Iterable, MediaTakenPhase, OperationalError,
    OrganizationScope, Principal, ProcessReason, RbacService, ResidentBindingRepository,
    SQLAlchemyError, ScopeType, Session, Settings, SlaClock, SlaClockState, SlaPolicyVersion,
    StorageProvider, T, WORK_ORDER_CANCEL, WORK_ORDER_CREATE, WORK_ORDER_DECIDE,
    WORK_ORDER_DISPATCH, WorkOrder, WorkOrderActorType, WorkOrderCategory, WorkOrderContent,
    WorkOrderErrorCode, WorkOrderEvent, WorkOrderFeedback, WorkOrderLog, WorkOrderPriority,
    WorkOrderProcess, WorkOrderRepository, WorkOrderSeverity, WorkOrderSlaApplication,
    WorkOrderStatus, _ACTIVE_ASSIGNMENT_UNIQUE, _HOUSE_SCOPE_FK, _IDEMPOTENCY_UNIQUE,
    _STAFF_ESCALATION_REASONS, build_file_scanner_provider, build_storage_provider,
    compute_sla_due_at, content_fingerprint, datetime, get_settings, inspect,
    loose_content_fingerprint, require_legal_transition,
    retryable_mysql_transaction_error_code, select, sha256, sla_warning_lead_minutes,
    sla_windows_for_priority, timedelta, utc_now
)


class WorkOrderLifecycleMixin:
    """Method group mixed into `WorkOrderService`."""

    def __init__(
        self,
        session: Session,
        repository: WorkOrderRepository | None = None,
        employee_repository: EmployeeRepository | None = None,
        resident_repository: ResidentBindingRepository | None = None,
        audit_service: AuditService | None = None,
        ai_repository: AiRepository | None = None,
        principal_loader: Callable[[int], Principal] | None = None,
        storage: StorageProvider | None = None,
        file_scanner: FileScannerProvider | None = None,
        settings: Settings | None = None,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._session = session
        self._repository = repository or WorkOrderRepository(session)
        self._employees = employee_repository or EmployeeRepository(session)
        self._residents = resident_repository or ResidentBindingRepository(session)
        self._audit_service = audit_service or AuditService(session)
        self._analysis = ai_repository or AiRepository(session)
        self._principal_loader = principal_loader or AuthService(
            session,
            IdentityRepository(session),
        ).load_principal
        self._settings = settings or get_settings()
        self._storage = storage or build_storage_provider(self._settings)
        self._file_scanner = file_scanner or build_file_scanner_provider(
            self._settings
        )
        self._clock = clock
        # PA-169 (t150): whether the last create call answered with an existing
        # work order instead of inserting one. Read by the API layer only.
        self._last_create_deduplicated = False
    @property
    def last_create_deduplicated(self) -> bool:
        """True when the previous ``create_work_order`` returned a duplicate."""

        return self._last_create_deduplicated
    def _content_fingerprint(
        self,
        text: str | None,
        file_ids: tuple[int, ...],
    ) -> tuple[str, str]:
        """Content identities of a report being created.

        Returns ``(strict, loose)``: the strict form keeps punctuation as a token
        boundary (PA-169/t150), the loose one drops every boundary so a resident
        who types the same sentence with a different comma is still recognised as
        repeating themselves (PA-169/t154).
        """

        digests = []
        for file_id in file_ids:
            stored = self._repository.get_file(file_id)
            if stored is not None and stored.content_hash:
                digests.append(stored.content_hash)
        return (
            content_fingerprint(text, digests),
            loose_content_fingerprint(text, digests),
        )
    def create_work_order(
        self,
        *,
        actor: Principal,
        house_id: int,
        request_id: str,
        idempotency_key: str,
        contact_phone: str | None = None,
        text: str | None = None,
        file_ids: tuple[int, ...] = (),
        category: str | None = None,
        priority: str | None = None,
        severity: str | None = None,
        claimed_tenant_id: int | None = None,
        claimed_property_id: int | None = None,
    ) -> WorkOrder:
        self._require_positive_id(house_id, "house_id")
        self._validate_request_id(request_id)
        self._validate_idempotency_key(idempotency_key)
        from app.domain.phone import normalize_phone

        contact = normalize_phone(contact_phone)
        if contact is None or not contact.isascii():
            raise AppError(code=WorkOrderErrorCode.INVALID_INPUT,
                           message="A valid contact telephone is required", status_code=422)
        self._last_create_deduplicated = False
        record, created = self._execute(
            lambda: self._create_work_order(
                actor=actor,
                house_id=house_id,
                request_id=request_id,
                idempotency_key=idempotency_key,
                contact_phone=contact,
                text=text,
                file_ids=file_ids,
                category=category,
                priority=priority,
                severity=severity,
                claimed_tenant_id=claimed_tenant_id,
                claimed_property_id=claimed_property_id,
            ),
            analysis_request_id=request_id,
        )
        if created:
            self._enqueue_analysis(record.id, request_id, version=record.version)
        return record
    def _enqueue_analysis(self, work_order_id: int, request_id: str, *, version: int | None = None) -> None:
        if self._settings.durable_tasks_enabled:
            return  # Already persisted in the business transaction; publisher is the backstop.
        if not self._settings.should_enqueue_ai_on_create:
            return
        from app.worker.enqueue import enqueue_celery_task
        from app.worker.tasks import analyze_work_order_task

        queued = enqueue_celery_task(
            settings=self._settings,
            celery_task=analyze_work_order_task,
            args=(work_order_id, request_id),
            event="ai_enqueue_failed",
            extra={"work_order_id": work_order_id},
            headers={"analysis_expected_version": version} if version is not None else None,
        )
        if not queued and self._settings.app_env == "production":
            # PA-104-A: never commit business state whose analysis never reached a queue.
            # RAISE means the owned transaction rolls back; no orphan "DB row / no task" state.
            raise AppError(
                code=AiErrorCode.ENQUEUE_FAILED,
                message="Analysis queue unavailable; request was not committed",
                status_code=503,
            )
    def append_content(
        self,
        *,
        actor: Principal,
        work_order_id: int,
        request_id: str,
        text: str | None = None,
        file_ids: tuple[int, ...] = (),
        taken_phase: str | None = None,
        version: int | None = None,
    ) -> WorkOrder:
        self._validate_request_id(request_id)
        record = self._execute(
            lambda: self._append_content(
                actor=actor,
                work_order_id=work_order_id,
                request_id=request_id,
                text=text,
                file_ids=file_ids,
                taken_phase=taken_phase,
                version=version,
            ),
            analysis_request_id=request_id,
        )
        if record.status == WorkOrderStatus.PENDING_ANALYSIS.value:
            self._enqueue_analysis(record.id, request_id, version=record.version)
        return record
    def cancel(
        self,
        *,
        actor: Principal,
        work_order_id: int,
        request_id: str,
        reason: str | None = None,
        version: int | None = None,
    ) -> WorkOrder:
        self._validate_request_id(request_id)
        record = self._execute(
            lambda: self._cancel(
                actor=actor,
                work_order_id=work_order_id,
                request_id=request_id,
                reason=reason,
                version=version,
            )
        )

        from app.worker.enqueue import schedule_dispatch_recovery
        schedule_dispatch_recovery(self._settings)
        return record
    def confirm(
        self,
        *,
        actor: Principal,
        work_order_id: int,
        request_id: str,
        version: int | None = None,
    ) -> WorkOrder:
        self._validate_request_id(request_id)
        record = self._execute(
            lambda: self._confirm(
                actor=actor,
                work_order_id=work_order_id,
                request_id=request_id,
                version=version,
            )
        )

        from app.worker.enqueue import schedule_dispatch_recovery
        schedule_dispatch_recovery(self._settings)
        return record
    def reopen(
        self,
        *,
        actor: Principal,
        work_order_id: int,
        request_id: str,
        reason: str,
        version: int | None = None,
    ) -> WorkOrder:
        self._validate_request_id(request_id)
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 255:
            raise AppError(
                code=WorkOrderErrorCode.INVALID_INPUT,
                message="reason must contain 1 to 255 characters",
                status_code=422,
            )
        record = self._execute(
            lambda: self._reopen(
                actor=actor,
                work_order_id=work_order_id,
                request_id=request_id,
                reason=reason.strip(),
                version=version,
            ),
            analysis_request_id=request_id,
        )
        if record.status == WorkOrderStatus.PENDING_ASSIGNMENT.value:
            self._enqueue_analysis(record.id, request_id, version=record.version)
        return record
    def rate(
        self,
        *,
        actor: Principal,
        work_order_id: int,
        request_id: str,
        rating: int,
        comment: str | None = None,
    ) -> WorkOrder:
        self._validate_request_id(request_id)
        if rating < 1 or rating > 5:
            raise AppError(
                code=WorkOrderErrorCode.INVALID_INPUT,
                message="rating is invalid",
                status_code=422,
            )
        return self._execute(
            lambda: self._rate(
                actor=actor,
                work_order_id=work_order_id,
                request_id=request_id,
                rating=rating,
                comment=comment,
            )
        )
    def escalate(
        self,
        *,
        actor: Principal,
        work_order_id: int,
        request_id: str,
        process_reason: str,
        reason: str,
        version: int | None = None,
    ) -> WorkOrder:
        self._validate_request_id(request_id)
        if not reason.strip():
            raise AppError(
                code=WorkOrderErrorCode.INVALID_INPUT,
                message="reason is required",
                status_code=422,
            )
        return self._execute(
            lambda: self._escalate(
                actor=actor,
                work_order_id=work_order_id,
                request_id=request_id,
                process_reason=process_reason,
                reason=reason.strip(),
                version=version,
            )
        )
    def _create_work_order(
        self,
        *,
        actor: Principal,
        house_id: int,
        request_id: str,
        idempotency_key: str,
        contact_phone: str,
        text: str | None,
        file_ids: tuple[int, ...],
        category: str | None,
        priority: str | None,
        severity: str | None,
        claimed_tenant_id: int | None,
        claimed_property_id: int | None,
    ) -> tuple[WorkOrder, bool]:
        from app.service.identity_write_guard import lock_active_identity

        lock_active_identity(self._session, actor.user_id)
        current = self._reload_actor(actor)
        cleaned_text = self._optional_text(text)
        if cleaned_text is None and not file_ids:
            raise AppError(
                code=WorkOrderErrorCode.CONTENT_REQUIRED,
                message="Work order content requires text or media",
                status_code=422,
            )
        request_hash = self._request_hash(
            house_id=house_id,
            contact_phone=contact_phone,
            text=cleaned_text,
            file_ids=file_ids,
            category=category,
            priority=priority,
            severity=severity,
        )
        existing = self._repository.get_idempotency(
            user_id=current.user_id,
            idempotency_key=idempotency_key,
        )
        if existing is not None:
            if existing.request_hash != request_hash:
                raise AppError(
                    code=WorkOrderErrorCode.IDEMPOTENCY_CONFLICT,
                    message="Idempotency key was reused with a different request",
                    status_code=409,
                )
            record = self._repository.get_work_order(existing.resource_id)
            if record is None:
                raise AppError(
                    code=WorkOrderErrorCode.CONFLICT,
                    message="Idempotent work order could not be loaded",
                    status_code=409,
                )
            return record, False
        house = self._residents.find_active_relation(
            user_id=current.user_id,
            house_id=house_id,
            now=self._clock(),
        )
        owned = self._residents.get_active_house_for_user(
            user_id=current.user_id,
            house_id=house_id,
            now=self._clock(),
        )
        if house is None or owned is None:
            registered = self._repository.get_house(house_id)
            if registered is None:
                raise AppError(
                    code=WorkOrderErrorCode.NOT_FOUND,
                    message="Work order was not found",
                    status_code=404,
                )
            raise AppError(
                code=WorkOrderErrorCode.HOUSE_NOT_OWNED,
                message="Caller does not have an effective house relation",
                status_code=403,
            )
        if (
            claimed_tenant_id is not None
            and claimed_tenant_id != owned.tenant_id
        ) or (
            claimed_property_id is not None
            and claimed_property_id != owned.property_id
        ):
            raise AppError(
                code=WorkOrderErrorCode.SCOPE_MISMATCH,
                message="Declared scope does not match the target house",
                status_code=422,
            )
        scope = OrganizationScope(
            ScopeType.PROPERTY,
            tenant_id=owned.tenant_id,
            property_id=owned.property_id,
        )
        RbacService.require_permission(current, WORK_ORDER_CREATE, scope)
        # PA-169 (t150): a resident who re-files the same report within the window
        # gets the work order they already have. Scoped to this resident and this
        # house: two residents reporting the same thing are two reports.
        fingerprint, loose_fingerprint = self._content_fingerprint(
            cleaned_text, file_ids
        )
        duplicate = self._repository.find_duplicate_content(
            house_id=owned.id,
            reporter_user_id=current.user_id,
            content_fingerprint=fingerprint,
            content_fingerprint_loose=loose_fingerprint,
            since=self._clock() - timedelta(minutes=DUPLICATE_WINDOW_MINUTES),
        )
        if duplicate is not None:
            self._last_create_deduplicated = True
            return duplicate, False
        category_value = self._parse_enum(
            category, WorkOrderCategory, WorkOrderCategory.OTHER, "category"
        )
        priority_value = self._parse_enum(
            priority, WorkOrderPriority, WorkOrderPriority.MEDIUM, "priority"
        )
        severity_value = self._parse_enum(
            severity, WorkOrderSeverity, WorkOrderSeverity.LOW, "severity"
        )
        now = self._clock()
        policy = self._repository.find_sla_policy(
            tenant_id=owned.tenant_id,
            property_id=owned.property_id,
            category=category_value.value,
            priority=priority_value.value,
        )
        respond_minutes, complete_minutes = sla_windows_for_priority(priority_value)
        if policy is not None:
            respond_minutes = policy.respond_minutes
            complete_minutes = policy.complete_minutes
        versioning_available = self._sla_versioning_available()
        sla_version = None
        accept_minutes = respond_minutes
        warning_delta = sla_warning_lead_minutes(complete_minutes)
        escalation_minutes = 0
        if versioning_available and policy is not None:
            sla_version = self._session.scalar(
                select(SlaPolicyVersion)
                .where(SlaPolicyVersion.policy_id == policy.id)
                .order_by(SlaPolicyVersion.version_no.desc(), SlaPolicyVersion.id.desc())
                .limit(1)
            )
            if sla_version is not None:
                accept_minutes = sla_version.accept_minutes
                respond_minutes = sla_version.respond_minutes
                complete_minutes = sla_version.complete_minutes
                warning_delta = sla_version.warning_lead_minutes
                escalation_minutes = sla_version.escalation_minutes
        due_at = compute_sla_due_at(
            created_at=now,
            priority=priority_value,
            complete_minutes=complete_minutes,
        )
        record = self._repository.add_work_order(
            WorkOrder(
                tenant_id=owned.tenant_id,
                property_id=owned.property_id,
                house_id=owned.id,
                reporter_user_id=current.user_id,
                contact_phone_e164=contact_phone,
                content_fingerprint=fingerprint,
                content_fingerprint_loose=loose_fingerprint,
                status=WorkOrderStatus.PENDING_ANALYSIS.value,
                process_reason=ProcessReason.NONE.value,
                category=category_value.value,
                severity=severity_value.value,
                priority=priority_value.value,
                sla_policy_id=None if policy is None else policy.id,
                sla_due_at=due_at,
                version=1,
            )
        )
        self._repository.add_content(
            WorkOrderContent(
                tenant_id=record.tenant_id,
                property_id=record.property_id,
                work_order_id=record.id,
                text=cleaned_text,
            )
        )
        self._attach_media(
            record,
            actor=current,
            file_ids=file_ids,
            taken_phase=MediaTakenPhase.REPORT,
        )
        if versioning_available:
            self._session.add(
                WorkOrderSlaApplication(
                    work_order_id=record.id,
                    policy_version_id=None if sla_version is None else sla_version.id,
                    source=(
                        "CODE_DEFAULT"
                        if policy is None
                        else "TENANT" if policy.property_id is None else "PROPERTY"
                    ),
                    accept_minutes=accept_minutes,
                    respond_minutes=respond_minutes,
                    complete_minutes=complete_minutes,
                    warning_lead_minutes=warning_delta,
                    escalation_minutes=escalation_minutes,
                    applied_at=now,
                )
            )
        self._repository.add_sla_clock(
            SlaClock(
                tenant_id=record.tenant_id,
                property_id=record.property_id,
                work_order_id=record.id,
                accept_due_at=now + timedelta(minutes=accept_minutes),
                respond_due_at=now + timedelta(minutes=respond_minutes),
                complete_due_at=due_at,
                warning_at=due_at - timedelta(minutes=warning_delta),
                state=SlaClockState.OPEN.value,
            )
        )
        self._write_log(
            record,
            actor=current,
            actor_type=WorkOrderActorType.RESIDENT,
            from_status=None,
            to_status=WorkOrderStatus.PENDING_ANALYSIS,
            event=WorkOrderEvent.CREATE,
            request_id=request_id,
        )
        self._repository.add_idempotency(
            IdempotencyRecord(
                idempotency_key=idempotency_key,
                user_id=current.user_id,
                tenant_id=record.tenant_id,
                property_id=record.property_id,
                resource_type="WORK_ORDER",
                resource_id=record.id,
                request_hash=request_hash,
            )
        )
        self._append_audit(
            action=AuditAction.WORK_ORDER_CREATED,
            resource_type=AuditResourceType.WORK_ORDER,
            actor_user_id=current.user_id,
            resource_id=record.id,
            request_id=request_id,
            tenant_id=record.tenant_id,
            property_id=record.property_id,
            details={
                "work_order_id": record.id,
                "status": record.status,
                "priority": record.priority,
                "category": record.category,
            },
        )
        self._notify_users(
            record,
            user_ids=(record.reporter_user_id,),
            title="工单已创建",
            body="工单已提交，系统正在分析并安排处理",
        )
        return record, True
    def _append_content(
        self,
        *,
        actor: Principal,
        work_order_id: int,
        request_id: str,
        text: str | None,
        file_ids: tuple[int, ...],
        taken_phase: str | None,
        version: int | None = None,
    ) -> WorkOrder:
        current = self._reload_actor(actor)
        record = self._lock_work_order(work_order_id)
        self._authorize_read(current, record)
        if record.reporter_user_id != current.user_id:
            raise AppError(
                code=IamErrorCode.FORBIDDEN,
                message="Permission denied",
                status_code=403,
            )
        self._assert_version(record, version)
        if record.status in {
            WorkOrderStatus.COMPLETED.value,
            WorkOrderStatus.CANCELLED.value,
            WorkOrderStatus.CLOSED.value,
        }:
            raise AppError(
                code=WorkOrderErrorCode.ALREADY_PROCESSED,
                message="Work order can no longer be updated",
                status_code=409,
            )
        cleaned = self._optional_text(text)
        if cleaned is None and not file_ids:
            raise AppError(
                code=WorkOrderErrorCode.CONTENT_REQUIRED,
                message="Append requires text or media",
                status_code=422,
            )
        phase = MediaTakenPhase.REPORT
        if taken_phase is not None:
            phase = self._parse_enum(
                taken_phase,
                MediaTakenPhase,
                MediaTakenPhase.REPORT,
                "taken_phase",
            )
            if phase is not MediaTakenPhase.REPORT:
                raise AppError(
                    code=WorkOrderErrorCode.INVALID_INPUT,
                    message="Resident append media must use the report phase",
                    status_code=422,
                )
        content = self._repository.get_content(record.id)
        if content is not None and cleaned is not None:
            notes = content.extra_notes or ""
            content.extra_notes = f"{notes}\n{cleaned}".strip() if notes else cleaned
        self._attach_media(record, actor=current, file_ids=file_ids, taken_phase=phase)
        # Content mutations participate in optimistic concurrency and make an
        # in-flight analysis snapshot stale before its result can be applied.
        record.version += 1
        scope_review_invalidated = False
        if record.status == WorkOrderStatus.PENDING_ANALYSIS.value:
            analysis = self._analysis.get_current_analysis_for_update(record.id)
            if (analysis is not None and analysis.status == "LOW_CONFIDENCE"
                    and analysis.error_code == AiErrorCode.OUT_OF_SCOPE):
                # A new resident version must not retain an old pure-scope
                # conclusion while its fresh analysis is queued. The original
                # reason and success audit remain available for history.
                analysis.status = "STALE"
                scope_review_invalidated = True
        self._write_log(
            record,
            actor=current,
            actor_type=WorkOrderActorType.RESIDENT,
            from_status=WorkOrderStatus(record.status),
            to_status=WorkOrderStatus(record.status),
            event=WorkOrderEvent.APPEND,
            request_id=request_id,
            reason_code="SCOPE_REVIEW_INVALIDATED_BY_APPEND" if scope_review_invalidated else None,
            reason_detail=cleaned,
        )
        self._session.flush()
        return record
    def _cancel(
        self,
        *,
        actor: Principal,
        work_order_id: int,
        request_id: str,
        reason: str | None,
        version: int | None,
    ) -> WorkOrder:
        current = self._reload_actor(actor)
        record = self._lock_work_order(work_order_id)
        scope = self._property_scope(record)
        is_reporter = record.reporter_user_id == current.user_id
        active_assignee_user_id = None
        if is_reporter:
            RbacService.require_permission(current, WORK_ORDER_CANCEL, scope)
            if record.status not in {
                WorkOrderStatus.PENDING_ANALYSIS.value,
                WorkOrderStatus.PENDING_ASSIGNMENT.value,
            }:
                raise AppError(
                    code=WorkOrderErrorCode.INVALID_TRANSITION,
                    message="Resident may cancel only before assignment",
                    status_code=409,
                )
        else:
            # WORK_ORDER_CANCEL is also granted to residents so they can
            # withdraw their own pending reports.  It is not, by itself, an
            # ownership grant for another resident's work order.  Reuse the
            # existing read/assignment/management policy before allowing a
            # non-reporter cancellation.
            # An assigned STAFF who also has RESIDENT cancellation permission
            # must not combine those grants into manager powers over others.
            if not (
                current.has_permission(WORK_ORDER_DISPATCH, scope)
                or current.has_permission(WORK_ORDER_DECIDE, scope)
            ):
                raise AppError(
                    code=IamErrorCode.FORBIDDEN,
                    message="Manager permission is required to cancel another report",
                    status_code=403,
                )
            self._authorize_read(current, record)
            RbacService.require_permission(current, WORK_ORDER_CANCEL, scope)
            active_assignee_user_id = self._repository.get_active_assignee_user_id(
                record.id
            )
        self._assert_version(record, version)
        self._apply_transition(
            record,
            event=WorkOrderEvent.CANCEL,
            to_status=WorkOrderStatus.CANCELLED,
            actor=current,
            actor_type=self._actor_type(current, record),
            request_id=request_id,
            reason_code="CANCELLED",
            reason_detail=self._optional_text(reason),
        )
        self._end_active_assignment(
            record,
            now=self._clock(),
            status=AssignmentStatus.ENDED,
        )
        self._stop_sla(record)
        self._append_audit(
            action=AuditAction.WORK_ORDER_CANCELLED,
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
            user_ids=(record.reporter_user_id, active_assignee_user_id),
            exclude_user_ids=(current.user_id,),
            title="工单已撤销",
            body="工单已撤销，请查看详情",
        )
        return record
    def _confirm(
        self,
        *,
        actor: Principal,
        work_order_id: int,
        request_id: str,
        version: int | None,
    ) -> WorkOrder:
        current = self._reload_actor(actor)
        record = self._lock_work_order(work_order_id)
        self._require_reporter(current, record)
        self._assert_version(record, version)
        process = self._repository.get_current_process(record.id)
        self._repository.add_feedback(
            WorkOrderFeedback(
                tenant_id=record.tenant_id,
                property_id=record.property_id,
                work_order_id=record.id,
                process_id=None if process is None else process.id,
                actor_user_id=current.user_id,
                confirmed=1,
                unresolved_flag=0,
            )
        )
        self._apply_transition(
            record,
            event=WorkOrderEvent.CONFIRM,
            to_status=WorkOrderStatus.COMPLETED,
            actor=current,
            actor_type=WorkOrderActorType.RESIDENT,
            request_id=request_id,
            reason_code="CONFIRMED",
        )
        self._end_active_assignment(
            record,
            now=self._clock(),
            status=AssignmentStatus.ENDED,
        )
        self._apply_transition(
            record,
            event=WorkOrderEvent.ARCHIVE,
            to_status=WorkOrderStatus.CLOSED,
            actor=current,
            actor_type=WorkOrderActorType.SYSTEM,
            request_id=request_id,
            reason_code="ARCHIVED",
        )
        record.closed_at = self._clock()
        self._stop_sla(record)
        self._append_audit(
            action=AuditAction.WORK_ORDER_CLOSED,
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
            user_ids=(
                record.reporter_user_id,
                *self._repository.list_sla_management_user_ids(
                    tenant_id=record.tenant_id,
                    property_id=record.property_id,
                ),
            ),
            title="工单已完成",
            body="住户已确认处理结果，工单已完成",
        )
        return record
    def _reopen(
        self,
        *,
        actor: Principal,
        work_order_id: int,
        request_id: str,
        reason: str,
        version: int | None,
    ) -> WorkOrder:
        current = self._reload_actor(actor)
        record = self._lock_work_order(work_order_id)
        self._require_reporter(current, record)
        self._assert_version(record, version)
        assignment = self._repository.get_active_assignment_for_update(record.id)
        employee = None
        if assignment is not None:
            employee = self._employees.get_employee(assignment.employee_id)
        eligible = (
            employee is not None
            and self._eligibility_error(
                record,
                employee,
                ignore_capacity=True,
            )
            is None
        )
        process = self._repository.get_current_process(record.id)
        self._repository.add_feedback(
            WorkOrderFeedback(
                tenant_id=record.tenant_id,
                property_id=record.property_id,
                work_order_id=record.id,
                process_id=None if process is None else process.id,
                actor_user_id=current.user_id,
                confirmed=0,
                unresolved_flag=1,
                comment=reason,
            )
        )
        if eligible and employee is not None:
            self._apply_transition(
                record,
                event=WorkOrderEvent.REOPEN,
                to_status=WorkOrderStatus.PROCESSING,
                actor=current,
                actor_type=WorkOrderActorType.RESIDENT,
                request_id=request_id,
                reason_code="REOPEN",
                reason_detail=reason,
            )
            next_cycle = 1 if process is None else process.cycle_no + 1
            self._repository.add_process(
                WorkOrderProcess(
                    tenant_id=record.tenant_id,
                    property_id=record.property_id,
                    work_order_id=record.id,
                    cycle_no=next_cycle,
                    assignee_id=employee.id,
                    started_at=self._clock(),
                )
            )
        else:
            self._end_active_assignment(
                record,
                now=self._clock(),
                status=AssignmentStatus.ENDED,
            )
            self._apply_transition(
                record,
                event=WorkOrderEvent.REOPEN,
                to_status=WorkOrderStatus.PENDING_ASSIGNMENT,
                actor=current,
                actor_type=WorkOrderActorType.RESIDENT,
                request_id=request_id,
                reason_code="REOPEN",
                reason_detail=reason,
            )
        record.resolved_at = None
        self._resume_reopened_sla(record)
        self._append_audit(
            action=AuditAction.WORK_ORDER_REOPENED,
            resource_type=AuditResourceType.WORK_ORDER,
            actor_user_id=current.user_id,
            resource_id=record.id,
            request_id=request_id,
            tenant_id=record.tenant_id,
            property_id=record.property_id,
            details={
                "work_order_id": record.id,
                "status": record.status,
                "reason_code": "REOPEN",
            },
        )
        if eligible and employee is not None:
            recipients: Iterable[int | None] = (employee.user_id,)
        else:
            recipients = self._repository.list_sla_management_user_ids(
                tenant_id=record.tenant_id,
                property_id=record.property_id,
            )
        self._notify_users(
            record,
            user_ids=recipients,
            exclude_user_ids=(current.user_id,),
            title="工单已重开",
            body="住户已重开工单，请查看并继续处理",
        )
        return record
    def _rate(
        self,
        *,
        actor: Principal,
        work_order_id: int,
        request_id: str,
        rating: int,
        comment: str | None,
    ) -> WorkOrder:
        current = self._reload_actor(actor)
        record = self._lock_work_order(work_order_id)
        self._require_reporter(current, record)
        if record.status not in {
            WorkOrderStatus.COMPLETED.value,
            WorkOrderStatus.CLOSED.value,
        }:
            raise AppError(
                code=WorkOrderErrorCode.INVALID_TRANSITION,
                message="Work order is not ready for rating",
                status_code=409,
            )
        if self._repository.get_rating_for_update(
            work_order_id=record.id,
            actor_user_id=current.user_id,
        ) is not None:
            raise AppError(
                code=WorkOrderErrorCode.ALREADY_PROCESSED,
                message="Work order has already been rated",
                status_code=409,
            )
        process = self._repository.get_current_process(record.id)
        self._repository.add_feedback(
            WorkOrderFeedback(
                tenant_id=record.tenant_id,
                property_id=record.property_id,
                work_order_id=record.id,
                process_id=None if process is None else process.id,
                actor_user_id=current.user_id,
                rating=rating,
                comment=self._optional_text(comment),
                unresolved_flag=0,
            )
        )
        self._write_log(
            record,
            actor=current,
            actor_type=WorkOrderActorType.RESIDENT,
            from_status=WorkOrderStatus(record.status),
            to_status=WorkOrderStatus(record.status),
            event=WorkOrderEvent.RATE,
            request_id=request_id,
        )
        return record
    def _escalate(
        self,
        *,
        actor: Principal,
        work_order_id: int,
        request_id: str,
        process_reason: str,
        reason: str,
        version: int | None,
    ) -> WorkOrder:
        current, record, _assignment, _employee = self._lock_assignee_action(
            actor, work_order_id, version, takeover=True
        )
        target = self._parse_enum(
            process_reason,
            ProcessReason,
            ProcessReason.ESCALATED,
            "process_reason",
        )
        if target not in _STAFF_ESCALATION_REASONS:
            raise AppError(
                code=WorkOrderErrorCode.INVALID_INPUT,
                message="process_reason is not an escalation reason",
                status_code=422,
            )
        self._apply_transition(
            record,
            event=WorkOrderEvent.ESCALATE,
            to_status=WorkOrderStatus.PROCESSING,
            actor=current,
            actor_type=self._actor_type(current, record),
            request_id=request_id,
            process_reason=target,
            reason_code=target.value,
            reason_detail=reason,
        )
        self._append_audit(
            action=AuditAction.WORK_ORDER_PROCESS_UPDATED,
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
            },
        )
        self._notify_users(
            record,
            user_ids=(
                record.reporter_user_id,
                *self._repository.list_sla_management_user_ids(
                    tenant_id=record.tenant_id,
                    property_id=record.property_id,
                ),
            ),
            exclude_user_ids=(current.user_id,),
            title="工单异常升级",
            body="工单已上报异常，请查看处理进度",
        )
        return record
    def _lock_work_order(self, work_order_id: int) -> WorkOrder:
        self._require_positive_id(work_order_id, "work_order_id")
        record = self._repository.get_work_order_for_update(work_order_id)
        if record is None:
            self._raise_not_found()
        return record
    def _apply_transition(
        self,
        record: WorkOrder,
        *,
        event: WorkOrderEvent,
        to_status: WorkOrderStatus,
        actor: Principal,
        actor_type: WorkOrderActorType,
        request_id: str,
        process_reason: ProcessReason | None = None,
        reason_code: str | None = None,
        reason_detail: str | None = None,
        receipt_handling: dict[str, object] | None = None,
    ) -> None:
        from_status = WorkOrderStatus(record.status)
        try:
            require_legal_transition(
                from_status=from_status,
                to_status=to_status,
                event=event,
            )
        except ValueError as exc:
            raise AppError(
                code=WorkOrderErrorCode.INVALID_TRANSITION,
                message="Work order transition is not allowed",
                status_code=409,
            ) from exc
        if to_status is WorkOrderStatus.PROCESSING:
            target_reason = process_reason or ProcessReason(record.process_reason)
        else:
            target_reason = ProcessReason.NONE
        record.status = to_status.value
        record.process_reason = target_reason.value
        record.version += 1
        self._write_log(
            record,
            actor=actor,
            actor_type=actor_type,
            from_status=from_status,
            to_status=to_status,
            event=event,
            request_id=request_id,
            reason_code=reason_code,
            reason_detail=reason_detail,
            receipt_handling=receipt_handling,
        )
        self._session.flush()
    def _write_log(
        self,
        record: WorkOrder,
        *,
        actor: Principal,
        actor_type: WorkOrderActorType,
        from_status: WorkOrderStatus | None,
        to_status: WorkOrderStatus,
        event: WorkOrderEvent,
        request_id: str,
        reason_code: str | None = None,
        reason_detail: str | None = None,
        receipt_handling: dict[str, object] | None = None,
    ) -> None:
        payload: dict[str, object] = {"event_code": event.value}
        if reason_code is not None:
            payload["reason_code"] = reason_code
        if reason_detail is not None:
            payload["reason"] = reason_detail
        if receipt_handling is not None:
            payload['receipt_handling_v1'] = dict(receipt_handling)
        self._repository.add_log(
            WorkOrderLog(
                tenant_id=record.tenant_id,
                property_id=record.property_id,
                work_order_id=record.id,
                actor_user_id=actor.user_id,
                actor_type=actor_type.value,
                from_status=None if from_status is None else from_status.value,
                to_status=to_status.value,
                process_reason=record.process_reason,
                event_code=event.value,
                request_id=request_id,
                payload_json=payload,
            )
        )
    def _stop_sla(self, record: WorkOrder) -> None:
        clock = self._repository.get_sla_clock(record.id)
        if clock is not None:
            clock.state = SlaClockState.STOPPED.value
    def _resume_reopened_sla(self, record: WorkOrder) -> None:
        """Resume the original deadline without repeating prior SLA alerts."""
        clock = self._repository.get_sla_clock_for_update(record.id)
        if clock is None or clock.state != SlaClockState.STOPPED.value:
            return
        events = {item.event_code for item in self._repository.list_logs(record.id)}
        if WorkOrderEvent.SLA_OVERDUE.value in events:
            clock.state = SlaClockState.OVERDUE.value
        elif WorkOrderEvent.SLA_WARNING.value in events:
            clock.state = SlaClockState.WARNING.value
        else:
            clock.state = SlaClockState.OPEN.value
    def _notify(
        self,
        *,
        tenant_id: int,
        property_id: int,
        user_id: int,
        title: str,
        body: str,
        resource_type: str,
        resource_id: int,
    ) -> None:
        from app.service.notification import NotificationService

        NotificationService(self._session, settings=self._settings).enqueue(
            tenant_id=tenant_id,
            property_id=property_id,
            user_id=user_id,
            title=title,
            body=body,
            resource_type=resource_type,
            resource_id=resource_id,
        )
    def _notify_users(
        self,
        record: WorkOrder,
        *,
        user_ids: Iterable[int | None],
        title: str,
        body: str,
        exclude_user_ids: Iterable[int] = (),
    ) -> None:
        excluded = {int(user_id) for user_id in exclude_user_ids}
        recipients = {
            int(user_id)
            for user_id in user_ids
            if user_id is not None and int(user_id) not in excluded
        }
        for user_id in sorted(recipients):
            self._notify(
                tenant_id=record.tenant_id,
                property_id=record.property_id,
                user_id=user_id,
                title=title,
                body=body,
                resource_type="WORK_ORDER",
                resource_id=record.id,
            )
    def _execute(self, operation: Callable[[], T], *, composing: bool = False, analysis_request_id: str | None = None) -> T:
        from app.core.database import run_composing_transaction, run_owned_transaction

        try:
            runner = run_composing_transaction if composing else run_owned_transaction
            def with_intent():
                result = operation()
                if (analysis_request_id is not None and self._settings.durable_tasks_enabled
                        and self._settings.should_enqueue_ai_on_create):
                    record = result[0] if isinstance(result, tuple) else result
                    created = result[1] if isinstance(result, tuple) else True
                    if created and record.status in {"PENDING_ANALYSIS", "PENDING_ASSIGNMENT"}:
                        from app.service.task_intent import ANALYZE, register_intent
                        register_intent(self._session, operation=ANALYZE, resource=record,
                                        request_id=analysis_request_id)
                return result
            return runner(self._session, with_intent)
        except AppError:
            raise
        except IntegrityError as exc:
            self._map_insert_error(exc)
            raise
        except OperationalError as exc:
            if retryable_mysql_transaction_error_code(exc) is not None:
                raise AppError(
                    code=WorkOrderErrorCode.CONFLICT,
                    message="Work order is busy; retry the operation",
                    status_code=409,
                    details={"retryable": True},
                ) from exc
            raise AppError(
                code=ErrorCode.INTERNAL_ERROR,
                message="Work order operation could not be completed",
                status_code=500,
            ) from exc
        except SQLAlchemyError as exc:
            raise AppError(
                code=ErrorCode.INTERNAL_ERROR,
                message="Work order operation could not be completed",
                status_code=500,
            ) from exc
    def _map_insert_error(self, error: IntegrityError) -> None:
        arguments = getattr(error.orig, "args", ())
        number = arguments[0] if arguments else None
        message = str(arguments[1]).casefold() if len(arguments) > 1 else ""
        if number == 1062:
            if any(name.casefold() in message for name in _ACTIVE_ASSIGNMENT_UNIQUE):
                raise AppError(
                    code=WorkOrderErrorCode.ALREADY_ASSIGNED,
                    message="Work order is already assigned",
                    status_code=409,
                ) from error
            if any(name.casefold() in message for name in _IDEMPOTENCY_UNIQUE):
                raise AppError(
                    code=WorkOrderErrorCode.IDEMPOTENCY_CONFLICT,
                    message="Idempotency key was reused with a different request",
                    status_code=409,
                ) from error
            raise AppError(
                code=WorkOrderErrorCode.CONFLICT,
                message="Work order conflicted with current data",
                status_code=409,
            ) from error
        if number == 1452 and any(
            name.casefold() in message for name in _HOUSE_SCOPE_FK
        ):
            raise AppError(
                code=WorkOrderErrorCode.SCOPE_MISMATCH,
                message="House does not belong to the declared property",
                status_code=422,
            ) from error
        if number == 1213:
            raise AppError(
                code=WorkOrderErrorCode.CONFLICT,
                message="Work order conflicted with current data",
                status_code=409,
            ) from error
        raise AppError(
            code=WorkOrderErrorCode.CONFLICT,
            message="Work order conflicted with current data",
            status_code=409,
        ) from error
    def _append_audit(self, **values: object) -> None:
        payload = dict(values)
        payload.setdefault("scope_type", ScopeType.PROPERTY)
        try:
            self._audit_service.append(**payload)
        except AppError:
            raise
        except SQLAlchemyError as exc:
            raise AppError(
                code=ErrorCode.INTERNAL_ERROR,
                message="Work order audit could not be written",
                status_code=500,
            ) from exc
    def _sla_versioning_available(self) -> bool:
        try:
            bind = self._session.get_bind()
            inspector = inspect(bind)
            return (
                inspector.has_table("work_order_sla_applications") is True
                and inspector.has_table("sla_policy_versions") is True
            )
        except (AttributeError, SQLAlchemyError):
            return False
    @staticmethod
    def _assert_version(record: WorkOrder, version: int | None) -> None:
        if version is not None and version != record.version:
            raise AppError(
                code=WorkOrderErrorCode.CONFLICT,
                message="Work order version conflicted",
                status_code=409,
            )
    @staticmethod
    def _parse_enum(value, enum_cls, default, field: str):
        if value is None or value == "":
            return default
        try:
            return enum_cls(value)
        except ValueError as exc:
            raise AppError(
                code=WorkOrderErrorCode.INVALID_INPUT,
                message=f"{field} is invalid",
                status_code=422,
                details={"field": field},
            ) from exc
    @staticmethod
    def _optional_text(value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None
    @staticmethod
    def _request_hash(
        *,
        house_id: int,
        contact_phone: str | None = None,
        text: str | None,
        file_ids: tuple[int, ...],
        category: str | None,
        priority: str | None,
        severity: str | None,
    ) -> str:
        payload = "|".join(
            (
                str(house_id),
                text or "",
                ",".join(str(item) for item in file_ids),
                category or "",
                priority or "",
                severity or "",
                contact_phone or "",
            )
        )
        return sha256(payload.encode("utf-8")).hexdigest()
    @staticmethod
    def _validate_idempotency_key(value: str) -> None:
        if (
            not isinstance(value, str)
            or not value.strip()
            or value != value.strip()
            or len(value) > 128
        ):
            raise AppError(
                code=WorkOrderErrorCode.INVALID_INPUT,
                message="Idempotency-Key is required",
                status_code=422,
            )
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
    @staticmethod
    def _require_positive_id(value: int, field: str) -> None:
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise AppError(
                code=WorkOrderErrorCode.INVALID_INPUT,
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
                code=WorkOrderErrorCode.CONFLICT,
                message="Work order conflicted with current data",
                status_code=409,
            )
