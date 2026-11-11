"""Files, receipts, media attachment and the feedback reply.

S6 moved these methods out of the old single-class module without editing their bodies.
"""
from .context import (
    AppError, AuditAction, AuditResourceType, Decimal, FileErrorCode, FileObject, IamErrorCode,
    MediaTakenPhase, Principal, ProcessReason, RbacService, WORK_ORDER_RESOLVE, WorkOrder,
    WorkOrderActorType, WorkOrderErrorCode, WorkOrderEvent, WorkOrderMedia, WorkOrderProcess,
    WorkOrderReceipt, WorkOrderStatus, _ALLOWED_MIME, _content_matches_mime,
    _filename_matches_mime, _registration_content_hash, admit_scanned_media, datetime,
    log_media_scan_skipped, logger, sha256, staff_may_submit_resolved_receipt
)


class WorkOrderMediaMixin:
    """Method group mixed into `WorkOrderService`."""

    def register_file(
        self,
        *,
        actor: Principal,
        mime_type: str,
        size_bytes: int,
        request_id: str,
        original_name: str | None = None,
        claimed_tenant_id: int | None = None,
        claimed_property_id: int | None = None,
    ) -> FileObject:
        self._require_active_actor(actor)
        self._validate_request_id(request_id)
        kind = _ALLOWED_MIME.get(mime_type.strip().casefold())
        if kind is None:
            raise AppError(
                code=FileErrorCode.UNSUPPORTED_TYPE,
                message="File type is not allowed",
                status_code=422,
            )
        normalized_mime = mime_type.strip().casefold()
        if not _filename_matches_mime(normalized_mime, original_name):
            raise AppError(
                code=FileErrorCode.UNSUPPORTED_TYPE,
                message="File extension does not match the declared type",
                status_code=422,
            )
        max_bytes = self.max_upload_bytes
        if size_bytes <= 0 or size_bytes > max_bytes:
            raise AppError(
                code=FileErrorCode.TOO_LARGE,
                message="File exceeds the upload size limit",
                status_code=422,
            )
        property_id = claimed_property_id
        tenant_id = claimed_tenant_id
        stored_holder: dict[str, object] = {}

        def _persist() -> FileObject:
            from app.service.identity.identity_write_guard import lock_active_identity

            lock_active_identity(self._session, actor.user_id)
            resolved_property_id = property_id
            resolved_tenant_id = tenant_id
            if resolved_property_id is not None:
                property_record = (
                    self._employees.get_property_in_tenant(
                        tenant_id=resolved_tenant_id or 0,
                        property_id=resolved_property_id,
                    )
                    if resolved_tenant_id
                    else self._repository.get_property(resolved_property_id)
                )
                if property_record is None:
                    raise AppError(
                        code=FileErrorCode.INVALID_INPUT,
                        message="Target property was not found",
                        status_code=422,
                    )
                resolved_tenant_id = property_record.tenant_id
            elif resolved_tenant_id is None:
                scoped = next(
                    (
                        grant.scope
                        for grant in actor.grants
                        if grant.scope.tenant_id is not None
                    ),
                    None,
                )
                if scoped is None or scoped.tenant_id is None:
                    raise AppError(
                        code=FileErrorCode.INVALID_INPUT,
                        message="A tenant context is required to register a file",
                        status_code=422,
                    )
                resolved_tenant_id = scoped.tenant_id
                resolved_property_id = scoped.property_id
            stored = self._storage.register_object(
                mime_type=normalized_mime,
                size_bytes=size_bytes,
                filename=original_name,
                tenant_id=resolved_tenant_id,
                property_id=resolved_property_id,
            )
            stored_holder["stored"] = stored
            record = FileObject(
                tenant_id=resolved_tenant_id,
                property_id=resolved_property_id,
                uploader_user_id=actor.user_id,
                content_hash="",
                mime_type=normalized_mime,
                size_bytes=size_bytes,
                storage_key=stored.storage_key,
                original_name=original_name,
                scanner_status=stored.scanner_status,
            )
            record.content_hash = _registration_content_hash(record)
            record = self._repository.add_file(record)
            return record

        record = self._execute(_persist)
        stored = stored_holder.get("stored")
        if stored is not None:
            record.upload_url = stored.upload_url  # type: ignore[attr-defined]
            record.upload_expires_in = stored.upload_expires_in  # type: ignore[attr-defined]
        return record
    def put_file_content(
        self,
        *,
        actor: Principal,
        file_id: int,
        content: bytes,
        request_id: str,
    ) -> FileObject:
        return self._execute(
            lambda: self._put_file_content(
                actor=actor,
                file_id=file_id,
                content=content,
                request_id=request_id,
            )
        )
    def _put_file_content(
        self,
        *,
        actor: Principal,
        file_id: int,
        content: bytes,
        request_id: str,
    ) -> FileObject:
        current = self._reload_actor(actor)
        self._validate_request_id(request_id)
        self._require_positive_id(file_id, "file_id")
        record = self._repository.get_file_for_update(file_id)
        if record is None or not self._actor_can_see_file_tenant(
            current, record.tenant_id
        ):
            raise AppError(
                code=FileErrorCode.NOT_FOUND,
                message="File was not found",
                status_code=404,
            )
        if record.uploader_user_id != current.user_id:
            raise AppError(
                code=IamErrorCode.FORBIDDEN,
                message="Permission denied",
                status_code=403,
            )
        max_bytes = self.max_upload_bytes
        if len(content) > max_bytes:
            raise AppError(
                code=FileErrorCode.TOO_LARGE,
                message="File exceeds the upload size limit",
                status_code=422,
            )
        if not content or len(content) != record.size_bytes:
            raise AppError(
                code=FileErrorCode.INVALID_INPUT,
                message="Uploaded content size does not match registration",
                status_code=422,
            )
        if not _content_matches_mime(record.mime_type, content):
            raise AppError(
                code=FileErrorCode.INVALID_INPUT,
                message="Uploaded content does not match the declared type",
                status_code=422,
            )
        from app.provider.errors import (
            ProviderInvalidOutputError,
            ProviderTimeoutError,
            ProviderUnavailableError,
        )

        placeholder_hash = _registration_content_hash(record)
        try:
            existing = self._storage.get_object(record.storage_key)
        except ProviderInvalidOutputError:
            if record.content_hash != placeholder_hash:
                logger.error(
                    "stored_object_missing",
                    file_id=record.id,
                    tenant_id=record.tenant_id,
                    property_id=record.property_id,
                )
                raise AppError(
                    code=FileErrorCode.STORAGE_UNAVAILABLE,
                    message="Stored file content is unavailable",
                    status_code=503,
                )
            existing = None
        except (ProviderTimeoutError, ProviderUnavailableError) as exc:
            raise AppError(
                code=FileErrorCode.STORAGE_UNAVAILABLE,
                message="Object storage is unavailable",
                status_code=503,
            ) from exc
        if existing is not None:
            existing_hash = sha256(existing).hexdigest()
            if (
                len(existing) != record.size_bytes
                or not _content_matches_mime(record.mime_type, existing)
                or record.content_hash not in {placeholder_hash, existing_hash}
            ):
                logger.error(
                    "stored_object_integrity_failed",
                    file_id=record.id,
                    tenant_id=record.tenant_id,
                    property_id=record.property_id,
                )
                raise AppError(
                    code=FileErrorCode.STORAGE_UNAVAILABLE,
                    message="Stored file failed integrity validation",
                    status_code=503,
                )
            if existing != content:
                raise AppError(
                    code=FileErrorCode.CONFLICT,
                    message="File content is immutable after upload",
                    status_code=409,
                )
            record.content_hash = existing_hash
            if record.scanner_status == "pending":
                record.scanner_status = self._file_scanner.scan(
                    content=existing,
                    mime_type=record.mime_type,
                )
            self._session.flush()
            return record

        try:
            self._storage.put_object(record.storage_key, content, record.mime_type)
        except (ProviderTimeoutError, ProviderUnavailableError) as exc:
            raise AppError(
                code=FileErrorCode.STORAGE_UNAVAILABLE,
                message="Object storage is unavailable",
                status_code=503,
            ) from exc
        record.content_hash = sha256(content).hexdigest()
        if record.scanner_status == "pending":
            record.scanner_status = self._file_scanner.scan(
                content=content,
                mime_type=record.mime_type,
            )
        self._session.flush()
        return record
    def _read_stored_file(self, record: FileObject) -> bytes:
        # D6: the verdict is decided in one place. `skipped_dev` is only usable
        # when the deployment declared the demo stub, and even then the use is
        # recorded as `media_scan_skipped` instead of being treated as scanned.
        admission = admit_scanned_media(record.scanner_status, self._settings)
        if not admission.usable:
            # PA-163: `details` used to be empty here, so a deployment with no
            # scanner produced a bare 409 that named neither the reason nor the
            # state of the file. The message stays the same for existing clients.
            raise AppError(
                code=FileErrorCode.INVALID_INPUT,
                message="File content is not ready for use",
                status_code=409,
                details={
                    "reason": admission.reason_code,
                    "scanner_status": record.scanner_status,
                },
            )
        if admission.demo_stub_skip:
            log_media_scan_skipped(logger, file_id=record.id, admission=admission)

        from app.provider.errors import (
            ProviderInvalidOutputError,
            ProviderTimeoutError,
            ProviderUnavailableError,
        )

        try:
            content = self._storage.get_object(record.storage_key)
        except ProviderInvalidOutputError as exc:
            raise AppError(
                code=FileErrorCode.NOT_FOUND,
                message="File content was not found",
                status_code=404,
            ) from exc
        except (ProviderTimeoutError, ProviderUnavailableError) as exc:
            raise AppError(
                code=FileErrorCode.STORAGE_UNAVAILABLE,
                message="Object storage is unavailable",
                status_code=503,
            ) from exc
        if (
            len(content) != record.size_bytes
            or sha256(content).hexdigest() != record.content_hash
            or not _content_matches_mime(record.mime_type, content)
        ):
            logger.error(
                "stored_object_integrity_failed",
                file_id=record.id,
                tenant_id=record.tenant_id,
                property_id=record.property_id,
            )
            raise AppError(
                code=FileErrorCode.STORAGE_UNAVAILABLE,
                message="Stored file failed integrity validation",
                status_code=503,
            )
        return content
    def submit_receipt(
        self,
        *,
        actor: Principal,
        work_order_id: int,
        request_id: str,
        problem_type: str,
        method: str,
        resolved: bool,
        materials: str | None = None,
        fee_amount: Decimal | None = None,
        fee_flag: str = "NONE",
        unresolved_reason: str | None = None,
        follow_up: str | None = None,
        before_file_ids: tuple[int, ...] = (),
        after_file_ids: tuple[int, ...] = (),
        file_ids: tuple[int, ...] = (),
        version: int | None = None,
        handling_mode: str | None = None,
        handling_note: str | None = None,
    ) -> WorkOrder:
        self._validate_request_id(request_id)
        from app.domain.work_order import normalize_handling

        try:
            handling_mode, handling_note = normalize_handling(handling_mode, handling_note)
        except ValueError as exc:
            raise AppError(code=WorkOrderErrorCode.INVALID_INPUT, message=str(exc), status_code=422) from exc
        if not problem_type.strip() or not method.strip():
            raise AppError(
                code=WorkOrderErrorCode.INVALID_INPUT,
                message="Receipt required fields are missing",
                status_code=422,
            )
        normalized_before_ids = tuple(dict.fromkeys(before_file_ids))
        normalized_after_ids = tuple(dict.fromkeys((*after_file_ids, *file_ids)))
        if set(normalized_before_ids).intersection(normalized_after_ids):
            raise AppError(
                code=WorkOrderErrorCode.INVALID_INPUT,
                message="Receipt media cannot be both before and after",
                status_code=422,
            )
        return self._execute(
            lambda: self._submit_receipt(
                actor=actor,
                work_order_id=work_order_id,
                request_id=request_id,
                problem_type=problem_type.strip(),
                method=method.strip(),
                resolved=resolved,
                materials=materials,
                fee_amount=fee_amount,
                fee_flag=fee_flag,
                unresolved_reason=unresolved_reason,
                follow_up=follow_up,
                before_file_ids=normalized_before_ids,
                after_file_ids=normalized_after_ids,
                version=version,
                handling_mode=handling_mode,
                handling_note=handling_note,
            )
        )
    def _submit_receipt(
        self,
        *,
        actor: Principal,
        work_order_id: int,
        request_id: str,
        problem_type: str,
        method: str,
        resolved: bool,
        materials: str | None,
        fee_amount: Decimal | None,
        fee_flag: str,
        unresolved_reason: str | None,
        follow_up: str | None,
        before_file_ids: tuple[int, ...],
        after_file_ids: tuple[int, ...],
        version: int | None,
        handling_mode: str | None = None,
        handling_note: str | None = None,
    ) -> WorkOrder:
        current, record, _assignment, _employee = self._lock_assignee_action(
            actor, work_order_id, version, takeover=True
        )
        RbacService.require_permission(
            current,
            WORK_ORDER_RESOLVE,
            self._property_scope(record),
        )
        if record.status != WorkOrderStatus.PROCESSING.value:
            raise AppError(
                code=WorkOrderErrorCode.ALREADY_PROCESSED,
                message="Work order is not in processing",
                status_code=409,
            )
        process = self._repository.get_current_process(record.id)
        if process is None:
            raise AppError(
                code=WorkOrderErrorCode.INVALID_TRANSITION,
                message="Processing cycle has not started",
                status_code=409,
            )
        if self._repository.get_receipt_for_process(process.id) is not None:
            raise AppError(
                code=WorkOrderErrorCode.ALREADY_PROCESSED,
                message="Receipt already exists for this process cycle",
                status_code=409,
            )
        from app.domain.work_order import handling_reason

        # A supervisor may submit on behalf of the assignee. SELF refers to
        # the actual submitter's employee identity, never the assigned worker.
        participant_id = (self._require_actor_employee(current).id
                          if handling_mode in {'SELF', 'ASSISTED'} else None)
        declaration = None if handling_mode is None else dict(
            process_id=process.id, mode=handling_mode, note=handling_note,
            reported_participant_employee_id=participant_id,
        )
        now = self._clock()
        if resolved:
            if not staff_may_submit_resolved_receipt(record.process_reason):
                raise AppError(
                    code=WorkOrderErrorCode.INVALID_TRANSITION,
                    message="Unresolved process reason cannot be completed",
                    status_code=409,
                )
            receipt = self._repository.add_receipt(
                WorkOrderReceipt(
                    tenant_id=record.tenant_id,
                    property_id=record.property_id,
                    work_order_id=record.id,
                    process_id=process.id,
                    problem_type=problem_type,
                    method=method,
                    materials=materials,
                    fee_amount=fee_amount,
                    fee_flag=fee_flag or "NONE",
                    resolved=1,
                    follow_up=follow_up,
                    finished_at=now,
                )
            )
            self._attach_media(
                record,
                actor=current,
                file_ids=before_file_ids,
                taken_phase=MediaTakenPhase.BEFORE,
                process_id=process.id,
                receipt_id=receipt.id,
            )
            self._attach_media(
                record,
                actor=current,
                file_ids=after_file_ids,
                taken_phase=MediaTakenPhase.AFTER,
                process_id=process.id,
                receipt_id=receipt.id,
            )
            process.ended_at = now
            record.resolved_at = now
            self._apply_transition(
                record,
                event=WorkOrderEvent.RECEIPT_RESOLVED,
                to_status=WorkOrderStatus.WAIT_CONFIRM,
                actor=current,
                actor_type=self._actor_type(current, record),
                request_id=request_id,
                reason_code="RESOLVED",
                reason_detail=handling_reason(handling_mode, handling_note),
                receipt_handling=None if declaration is None else dict(declaration, receipt_id=receipt.id),
            )
            self._append_audit(
                action=AuditAction.WORK_ORDER_RESOLVED,
                resource_type=AuditResourceType.WORK_ORDER,
                actor_user_id=current.user_id,
                resource_id=record.id,
                request_id=request_id,
                tenant_id=record.tenant_id,
                property_id=record.property_id,
                details={"work_order_id": record.id, "status": record.status},
            )
            self._notify(
                tenant_id=record.tenant_id,
                property_id=record.property_id,
                user_id=record.reporter_user_id,
                title="工单待确认",
                body="Please confirm the work order result",
                resource_type="WORK_ORDER",
                resource_id=record.id,
            )
            self._stop_sla(record)
        else:
            if unresolved_reason is None or not unresolved_reason.strip():
                raise AppError(
                    code=WorkOrderErrorCode.INVALID_INPUT,
                    message="unresolved_reason is required",
                    status_code=422,
                )
            target_reason = ProcessReason.INTERNAL_UNRESOLVED
            receipt = self._repository.add_receipt(
                WorkOrderReceipt(
                    tenant_id=record.tenant_id,
                    property_id=record.property_id,
                    work_order_id=record.id,
                    process_id=process.id,
                    problem_type=problem_type,
                    method=method,
                    materials=materials,
                    fee_amount=fee_amount,
                    fee_flag=fee_flag or "NONE",
                    resolved=0,
                    unresolved_reason=unresolved_reason.strip(),
                    follow_up=follow_up,
                    finished_at=now,
                )
            )
            self._attach_media(
                record,
                actor=current,
                file_ids=before_file_ids,
                taken_phase=MediaTakenPhase.BEFORE,
                process_id=process.id,
                receipt_id=receipt.id,
            )
            self._attach_media(
                record,
                actor=current,
                file_ids=after_file_ids,
                taken_phase=MediaTakenPhase.AFTER,
                process_id=process.id,
                receipt_id=receipt.id,
            )
            process.ended_at = now
            self._apply_transition(
                record,
                event=WorkOrderEvent.RECEIPT_UNRESOLVED,
                to_status=WorkOrderStatus.PROCESSING,
                actor=current,
                actor_type=self._actor_type(current, record),
                request_id=request_id,
                process_reason=target_reason,
                reason_code=target_reason.value,
                reason_detail=handling_reason(handling_mode, handling_note),
                receipt_handling=None if declaration is None else dict(declaration, receipt_id=receipt.id),
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
                title="工单异常待处理",
                body="工单未解决并已上报，请查看详情",
            )
        return record
    def append_receipt_media(
        self,
        *,
        actor: Principal,
        work_order_id: int,
        receipt_id: int,
        request_id: str,
        before_file_ids: tuple[int, ...] = (),
        after_file_ids: tuple[int, ...] = (),
        version: int | None = None,
    ) -> WorkOrder:
        self._validate_request_id(request_id)
        normalized_before_ids = tuple(dict.fromkeys(before_file_ids))
        normalized_after_ids = tuple(dict.fromkeys(after_file_ids))
        if not normalized_before_ids and not normalized_after_ids:
            raise AppError(
                code=WorkOrderErrorCode.CONTENT_REQUIRED,
                message="Receipt media is required",
                status_code=422,
            )
        if set(normalized_before_ids).intersection(normalized_after_ids):
            raise AppError(
                code=WorkOrderErrorCode.INVALID_INPUT,
                message="Receipt media cannot be both before and after",
                status_code=422,
            )
        return self._execute(
            lambda: self._append_receipt_media(
                actor=actor,
                work_order_id=work_order_id,
                receipt_id=receipt_id,
                request_id=request_id,
                before_file_ids=normalized_before_ids,
                after_file_ids=normalized_after_ids,
                version=version,
            )
        )
    def _append_receipt_media(
        self,
        *,
        actor: Principal,
        work_order_id: int,
        receipt_id: int,
        request_id: str,
        before_file_ids: tuple[int, ...],
        after_file_ids: tuple[int, ...],
        version: int | None,
    ) -> WorkOrder:
        current, record, _assignment, _employee = self._lock_assignee_action(
            actor, work_order_id, version, takeover=False
        )
        RbacService.require_permission(
            current,
            WORK_ORDER_RESOLVE,
            self._property_scope(record),
        )
        if record.status != WorkOrderStatus.WAIT_CONFIRM.value:
            raise AppError(
                code=WorkOrderErrorCode.INVALID_TRANSITION,
                message="Receipt media can only be supplemented while awaiting confirmation",
                status_code=409,
            )
        process = self._repository.get_current_process(record.id)
        receipt = (
            None
            if process is None
            else self._repository.get_receipt_for_process(process.id)
        )
        if receipt is None or receipt.id != receipt_id:
            raise AppError(
                code=WorkOrderErrorCode.INVALID_TRANSITION,
                message="Receipt is not current for this processing cycle",
                status_code=409,
            )
        self._attach_media(
            record,
            actor=current,
            file_ids=before_file_ids,
            taken_phase=MediaTakenPhase.BEFORE,
            process_id=process.id,
            receipt_id=receipt.id,
        )
        self._attach_media(
            record,
            actor=current,
            file_ids=after_file_ids,
            taken_phase=MediaTakenPhase.AFTER,
            process_id=process.id,
            receipt_id=receipt.id,
        )
        record.version += 1
        self._write_log(
            record,
            actor=current,
            actor_type=WorkOrderActorType.STAFF,
            from_status=WorkOrderStatus.WAIT_CONFIRM,
            to_status=WorkOrderStatus.WAIT_CONFIRM,
            event=WorkOrderEvent.RECEIPT_MEDIA_APPENDED,
            request_id=request_id,
            reason_code="RECEIPT_MEDIA_APPENDED",
        )
        self._append_audit(
            action=AuditAction.WORK_ORDER_RECEIPT_MEDIA_APPENDED,
            resource_type=AuditResourceType.WORK_ORDER,
            actor_user_id=current.user_id,
            resource_id=record.id,
            request_id=request_id,
            tenant_id=record.tenant_id,
            property_id=record.property_id,
            details={
                "work_order_id": record.id,
                "receipt_id": receipt.id,
                "process_id": process.id,
                "before_count": len(before_file_ids),
                "after_count": len(after_file_ids),
            },
        )
        self._session.flush()
        return record
    def feedback(
        self,
        *,
        actor: Principal,
        work_order_id: int,
        request_id: str,
        text: str | None = None,
        process_reason: str | None = None,
        file_ids: tuple[int, ...] = (),
        taken_phase: str | None = None,
        version: int | None = None,
    ) -> WorkOrder:
        self._validate_request_id(request_id)
        return self._execute(
            lambda: self._feedback(
                actor=actor,
                work_order_id=work_order_id,
                request_id=request_id,
                text=text,
                process_reason=process_reason,
                file_ids=file_ids,
                taken_phase=taken_phase,
                version=version,
            )
        )
    def _feedback(
        self,
        *,
        actor: Principal,
        work_order_id: int,
        request_id: str,
        text: str | None,
        process_reason: str | None,
        file_ids: tuple[int, ...],
        taken_phase: str | None,
        version: int | None,
    ) -> WorkOrder:
        current, record, _assignment, _employee = self._lock_assignee_action(
            actor, work_order_id, version, takeover=True
        )
        if record.status != WorkOrderStatus.PROCESSING.value:
            raise AppError(
                code=WorkOrderErrorCode.INVALID_TRANSITION,
                message="Feedback is only allowed while processing",
                status_code=409,
            )
        current_reason = ProcessReason(record.process_reason)
        target_reason = current_reason if process_reason is None else self._parse_enum(
            process_reason, ProcessReason, ProcessReason.NONE, "process_reason",
        )
        if target_reason != current_reason:
            raise AppError(
                code=WorkOrderErrorCode.INVALID_TRANSITION,
                message="Use escalation or a supervisor decision to change the processing reason",
                status_code=409,
            )
        cleaned = self._optional_text(text)
        if cleaned is not None:
            content = self._repository.get_content(record.id)
            if content is not None:
                notes = content.extra_notes or ""
                content.extra_notes = (
                    f"{notes}\n{cleaned}".strip() if notes else cleaned
                )
        phase = MediaTakenPhase.ON_SITE
        if taken_phase is not None:
            phase = self._parse_enum(
                taken_phase,
                MediaTakenPhase,
                MediaTakenPhase.ON_SITE,
                "taken_phase",
            )
        process_id: int | None = None
        if file_ids:
            process = self._repository.get_current_process(record.id)
            if process is None:
                raise AppError(
                    code=WorkOrderErrorCode.INVALID_TRANSITION,
                    message="Processing cycle has not started",
                    status_code=409,
                )
            process_id = process.id
        self._attach_media(
            record,
            actor=current,
            file_ids=file_ids,
            taken_phase=phase,
            process_id=process_id,
        )
        self._apply_transition(
            record,
            event=WorkOrderEvent.PROCESS_UPDATE,
            to_status=WorkOrderStatus.PROCESSING,
            actor=current,
            actor_type=self._actor_type(current, record),
            request_id=request_id,
            process_reason=target_reason,
            reason_code=target_reason.value,
            reason_detail=cleaned,
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
        return record
    def _attach_media(
        self,
        record: WorkOrder,
        *,
        actor: Principal,
        file_ids: tuple[int, ...],
        taken_phase: MediaTakenPhase,
        process_id: int | None = None,
        receipt_id: int | None = None,
    ) -> None:
        if not file_ids:
            return
        if taken_phase is MediaTakenPhase.REPORT:
            if process_id is not None or receipt_id is not None:
                raise AppError(
                    code=WorkOrderErrorCode.INVALID_INPUT,
                    message="Report media cannot be linked to a processing cycle",
                    status_code=422,
                )
        elif taken_phase is MediaTakenPhase.ON_SITE:
            if process_id is None or receipt_id is not None:
                raise AppError(
                    code=WorkOrderErrorCode.INVALID_INPUT,
                    message="On-site media requires its processing cycle",
                    status_code=422,
                )
        elif process_id is None or receipt_id is None:
            raise AppError(
                code=WorkOrderErrorCode.INVALID_INPUT,
                message="Receipt media requires its process and receipt",
                status_code=422,
            )

        if process_id is not None:
            process = self._repository.get_current_process(record.id)
            if (
                process is None
                or process.id != process_id
                or process.work_order_id != record.id
                or process.tenant_id != record.tenant_id
                or process.property_id != record.property_id
            ):
                raise AppError(
                    code=WorkOrderErrorCode.INVALID_TRANSITION,
                    message="Media process cycle is not current for this work order",
                    status_code=409,
                )
        if receipt_id is not None:
            receipt = self._repository.get_receipt_for_process(process_id)
            if (
                receipt is None
                or receipt.id != receipt_id
                or receipt.process_id != process_id
                or receipt.work_order_id != record.id
                or receipt.tenant_id != record.tenant_id
                or receipt.property_id != record.property_id
            ):
                raise AppError(
                    code=WorkOrderErrorCode.INVALID_TRANSITION,
                    message="Media receipt does not belong to this processing cycle",
                    status_code=409,
                )
        seen: set[int] = set()
        for file_id in file_ids:
            self._require_positive_id(file_id, "file_ids")
            if file_id in seen:
                continue
            seen.add(file_id)
            file_object = self._repository.get_file(file_id)
            if (
                file_object is None
                or file_object.tenant_id != record.tenant_id
                or file_object.property_id not in {None, record.property_id}
                or file_object.uploader_user_id != actor.user_id
            ):
                raise AppError(
                    code=FileErrorCode.NOT_FOUND,
                    message="File was not found",
                    status_code=404,
                )
            if not self._file_is_visible(
                actor, file_object,
                audience="property" if taken_phase is MediaTakenPhase.ON_SITE else "resident",
            ):
                self._raise_file_not_found()
            kind = _ALLOWED_MIME.get(file_object.mime_type)
            if kind is None:
                raise AppError(
                    code=FileErrorCode.UNSUPPORTED_TYPE,
                    message="File type is not allowed",
                    status_code=422,
                )
            self._read_stored_file(file_object)
            self._repository.add_media(
                WorkOrderMedia(
                    tenant_id=record.tenant_id,
                    property_id=record.property_id,
                    work_order_id=record.id,
                    kind=kind.value,
                    file_id=file_object.id,
                    taken_phase=taken_phase.value,
                    process_id=process_id,
                    receipt_id=receipt_id,
                    uploader_user_id=actor.user_id,
                )
            )
    def _end_current_process(self, record: WorkOrder, *, now: datetime) -> None:
        process = self._repository.get_current_process(record.id)
        if process is None or process.ended_at is not None:
            return
        process.ended_at = now
        self._session.flush()
    def _resume_receipted_process(self, record: WorkOrder, *, now: datetime) -> None:
        process = self._repository.get_current_process(record.id)
        if process is None or self._repository.get_receipt_for_process(process.id) is None:
            return
        assignment = self._repository.get_active_assignment_for_update(record.id)
        if assignment is None:
            raise AppError(
                code=WorkOrderErrorCode.INVALID_TRANSITION,
                message="An active assignment is required to continue processing",
                status_code=409,
            )
        if process.ended_at is None:
            process.ended_at = now
        self._repository.add_process(
            WorkOrderProcess(
                tenant_id=record.tenant_id,
                property_id=record.property_id,
                work_order_id=record.id,
                cycle_no=process.cycle_no + 1,
                assignee_id=assignment.employee_id,
                started_at=now,
            )
        )
        self._session.flush()
    @property
    def max_upload_bytes(self) -> int:
        """Maximum accepted upload payload, shared by registration and streaming."""

        return self._settings.upload_max_mb * 1024 * 1024
