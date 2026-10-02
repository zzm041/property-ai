"""Typed durable delivery/lease coordinator, not another business task engine."""
from dataclasses import dataclass
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import select

from app.core.database import begin_owned_transaction, require_owned_transaction
from app.core.time import utc_now
from app.model.task_intent import TaskIntent
from app.repository.task_intent import TaskIntentRepository

ANALYZE = "ANALYZE_WORK_ORDER"
INGEST = "INGEST_KNOWLEDGE"
DELIVER = "DELIVER_NOTIFICATION"
TERMINAL = frozenset(("SUCCEEDED", "FAILED", "CANCELLED"))
LEASE_KEY = "task_intent_execution"


class IntentLeaseLost(Exception):
    error_code = "TASK.LEASE_LOST"


@dataclass(frozen=True)
class IntentTiming:
    publish_lease: int = 15
    claim_deadline: int = 120
    execution_lease: int = 120
    heartbeat: int = 20
    execution_deadline: int = 900


def execution_limit(operation):
    if operation not in {ANALYZE, INGEST, DELIVER}:
        raise ValueError("Unsupported intent operation")
    return 4 if operation == DELIVER else 3


def due(row, now):
    if row.status in TERMINAL:
        return False
    if row.status in {"PUBLISHING", "PROCESSING"}:
        return row.lease_until is not None and row.lease_until <= now
    return row.available_at <= now


def owns(row, token, now):
    return (row is not None and row.status == "PROCESSING" and row.lease_token == token
            and row.lease_until is not None and row.lease_until > now
            and row.available_at > now)


def end(row, status, now, error=None):
    row.status, row.last_error_code = status, error
    row.lease_token = row.lease_until = None
    row.finished_at = now if status in TERMINAL else None


def require_execution_lease(session):
    """Call under domain mutex, before domain reads/writes; no extra transaction."""
    lease = session.info.get(LEASE_KEY)
    if not lease:
        return None
    require_owned_transaction(session)
    row = TaskIntentRepository(session).get(lease["id"], lock=True)
    if not owns(row, lease["token"], utc_now()):
        raise IntentLeaseLost()
    return row


def register_intent(session, *, operation, resource, request_id, version=None, scope=None):
    """Only authoritative freshly persisted resources may create generation1."""
    require_owned_transaction(session)
    execution_limit(operation)
    from app.model.work_order import WorkOrder
    from app.model.knowledge import KnowledgeIngestionJob, NotificationDelivery
    expected = {ANALYZE: WorkOrder, INGEST: KnowledgeIngestionJob, DELIVER: NotificationDelivery}[operation]
    if not isinstance(resource, expected) or resource.id is None:
        raise ValueError("Intent requires a persisted typed business resource")
    if operation == DELIVER:
        from app.model.ai import Notification
        notification = session.get(Notification, resource.notification_id)
        if notification is None:
            raise ValueError("Notification resource missing")
        tenant_id, property_id, version = notification.tenant_id, notification.property_id, 1
    else:
        tenant_id, property_id = resource.tenant_id, resource.property_id
        version = resource.version if version is None else version
    if (scope is not None and scope != (tenant_id, property_id)) or (property_id is None and operation != INGEST):
        raise ValueError("Intent resource scope mismatch")
    if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128 or any(ord(c) < 32 for c in request_id):
        raise ValueError("Invalid intent correlation marker")
    repo = TaskIntentRepository(session)
    existing = repo.for_resource(operation, resource.id, version)
    if existing:
        return existing
    return repo.add(TaskIntent(tenant_id=tenant_id, property_id=property_id, operation=operation,
        resource_id=resource.id, resource_version=version, generation=1, request_id=request_id,
        task_id=str(uuid4()), status="PENDING", publish_attempt=0, execution_attempt=0, available_at=utc_now()))


class TaskIntentService:
    def __init__(self, session, *, clock=utc_now, timing=None):
        self.session, self.repo, self.clock = session, TaskIntentRepository(session), clock
        self.timing = timing or IntentTiming()

    def _locked(self, intent_id):
        # Same order as AnalysisService: WorkOrder -> intent -> analysis/assignment.
        row = self.repo.get(intent_id)
        if row and row.operation == ANALYZE:
            from app.repository.work_order import WorkOrderRepository
            WorkOrderRepository(self.session).get_work_order_for_update(row.resource_id)
        return self.repo.get(intent_id, lock=True)

    def claim_publication(self, intent_id):
        with begin_owned_transaction(self.session):
            row, now = self._locked(intent_id), self.clock()
            if row is None or not due(row, now):
                return None
            if self._reconcile(row, now):
                return None
            if row.publish_attempt >= 5 or row.execution_attempt >= execution_limit(row.operation):
                self._fail(row, now, "TASK.RETRY_EXHAUSTED")
                return None
            row.publish_attempt += 1
            row.status, row.lease_token = "PUBLISHING", uuid4().hex
            row.lease_until = now + timedelta(seconds=self.timing.publish_lease)
            return {"id": row.id, "token": row.lease_token, "task_id": row.task_id, "attempt": row.publish_attempt}

    def publication_result(self, intent_id, token, *, sent):
        with begin_owned_transaction(self.session):
            row, now = self._locked(intent_id), self.clock()
            if row is None or row.status != "PUBLISHING" or row.lease_token != token:
                return False  # Worker may already have completed: never regress it.
            if sent:
                end(row, "PUBLISHED", now)
                row.published_at = now
                row.available_at = now + timedelta(seconds=self.timing.claim_deadline)
            elif row.publish_attempt >= 5:
                self._fail(row, now, "TASK.PUBLISH_EXHAUSTED")
            else:
                end(row, "RETRY_WAIT", now, "TASK.PUBLISH_UNAVAILABLE")
                row.available_at = now + timedelta(seconds=2 ** (row.publish_attempt - 1))
            return True

    def claim_execution(self, intent_id, task_id):
        with begin_owned_transaction(self.session):
            row, now = self._locked(intent_id), self.clock()
            if row is None or row.task_id != task_id or row.status in TERMINAL:
                return None
            if row.status == "PROCESSING" and row.lease_until > now:
                return None
            if row.status in {"PENDING", "RETRY_WAIT"} and row.available_at > now:
                return None
            if self._reconcile(row, now):
                return None
            if row.execution_attempt >= execution_limit(row.operation):
                self._fail(row, now, "TASK.EXECUTION_EXHAUSTED")
                return None
            row.execution_attempt += 1
            row.status, row.lease_token = "PROCESSING", uuid4().hex
            row.available_at = now + timedelta(seconds=self.timing.execution_deadline)
            row.lease_until = min(now + timedelta(seconds=self.timing.execution_lease), row.available_at)
            return {"id": row.id, "token": row.lease_token, "task_id": row.task_id,
                    "operation": row.operation, "resource_id": row.resource_id,
                    "resource_version": row.resource_version, "request_id": row.request_id,
                    "attempt": row.execution_attempt, "tenant_id": row.tenant_id, "property_id": row.property_id}

    def heartbeat(self, lease):
        with begin_owned_transaction(self.session):
            row, now = self.repo.get(lease["id"], lock=True), self.clock()
            if not owns(row, lease["token"], now):
                return False
            row.lease_until = min(now + timedelta(seconds=self.timing.execution_lease), row.available_at)
            return True

    def finish(self, lease, outcome, *, error=None, retryable=False):
        with begin_owned_transaction(self.session):
            row, now = self._locked(lease["id"]), self.clock()
            if not owns(row, lease["token"], now):
                return False
            # A failed COMMIT acknowledgement does not prove domain rollback.
            # Re-read committed outcomes in this fresh owned transaction even
            # on the last budgeted attempt, before making FAILED terminal.
            if outcome == "FAILED" and self._reconcile(row, now):
                return True
            if retryable and row.execution_attempt < execution_limit(row.operation):
                end(row, "RETRY_WAIT", now, error)
                row.available_at = now + timedelta(seconds=2 ** (row.execution_attempt - 1))
            elif outcome == "FAILED":
                self._fail(row, now, error or "TASK.PERMANENT_FAILURE")
            else:
                if outcome not in {"SUCCEEDED", "CANCELLED"}:
                    raise ValueError("Only explicit domain outcomes finish intents")
                end(row, outcome, now)
            return True

    def _reconcile(self, row, now):
        """Resolve committed outcomes before consuming/exhausting another attempt."""
        from app.model.work_order import WorkOrder
        from app.model.knowledge import KnowledgeIngestionJob, NotificationDelivery
        if row.operation in {INGEST, DELIVER}:
            model = KnowledgeIngestionJob if row.operation == INGEST else NotificationDelivery
            resource = self.session.get(model, row.resource_id, with_for_update=True, populate_existing=True)
            if resource is not None and resource.status == "SUCCEEDED":
                end(row, "SUCCEEDED", now)
                return True
            return False
        from app.repository.work_order import WorkOrderRepository
        work = WorkOrderRepository(self.session).get_work_order_for_update(row.resource_id)
        if work is None or work.status in {"CANCELLED", "CLOSED"}:
            end(row, "CANCELLED", now)
            return True
        from app.repository.work_order import WorkOrderRepository
        from app.repository.ai import AiRepository
        repo = WorkOrderRepository(self.session)
        latest = repo.latest_analysis_attempt(work.id)
        claim = latest.payload_json if latest else None
        if claim is None or claim.get("task_id") != row.task_id:
            if work.version != row.resource_version:
                end(row, "CANCELLED", now)
                return True
            return False
        analysis = AiRepository(self.session).get_current_analysis_for_update(work.id)
        if (analysis is None or analysis.id != claim["analysis_id"]
                or analysis.input_hash != claim["input_hash"]):
            end(row, "CANCELLED", now)
            return True
        expected = claim["work_order_version"] + (
            claim["from_status"] == "PENDING_ANALYSIS"
            or claim.get("attachment_completion") is True
        )
        if (analysis.status == "LOW_CONFIDENCE" and work.version == claim["work_order_version"]
                and work.status == claim["from_status"]):
            end(row, "SUCCEEDED", now)
            return True
        if analysis.status == "COMPLETED":
            assigned = work.current_assignment_id is not None and work.version == expected + 1
            waiting = work.status == "PENDING_ASSIGNMENT" and work.version == expected and repo.has_dispatch_wait_attempt(
                work_order_id=work.id, request_id=row.request_id)
            if assigned or waiting:
                end(row, "SUCCEEDED", now)
                return True
        return False

    def _fail(self, row, now, code):
        """Failure state and visible domain error in the same owned transaction."""
        from app.model.work_order import WorkOrder, WorkOrderLog
        from app.model.knowledge import KnowledgeIngestionJob, NotificationDelivery
        end(row, "FAILED", now, code)
        if row.operation == ANALYZE:
            from app.repository.work_order import WorkOrderRepository
            from app.repository.ai import AiRepository
            work = WorkOrderRepository(self.session).get_work_order_for_update(row.resource_id)
            if work and work.status in {"PENDING_ANALYSIS", "PENDING_ASSIGNMENT"} and work.current_assignment_id is None:
                from app.repository.work_order import WorkOrderRepository
                latest = WorkOrderRepository(self.session).latest_analysis_attempt(work.id)
                claim = latest.payload_json if latest else None
                expected = row.resource_version
                if claim and claim.get("task_id") == row.task_id:
                    current = AiRepository(self.session).get_current_analysis_for_update(work.id)
                    completion_committed = (
                        claim.get("attachment_completion") is True
                        and current is not None
                        and current.id == claim.get("analysis_id")
                        and current.input_hash == claim.get("input_hash")
                        and current.status == "COMPLETED"
                    )
                    expected = claim["work_order_version"] + (
                        work.status == "PENDING_ASSIGNMENT"
                        and (
                            claim["from_status"] == "PENDING_ANALYSIS"
                            or completion_committed
                        )
                    )
                if work.version == expected and (claim is None or claim.get("task_id") == row.task_id):
                    self.session.add(WorkOrderLog(tenant_id=work.tenant_id, property_id=work.property_id,
                        work_order_id=work.id, actor_type="SYSTEM", from_status=work.status, to_status=work.status,
                        process_reason=work.process_reason, event_code="AI_TASK_FAILED", request_id=row.request_id,
                        payload_json={"error_code": code, "task_id": row.task_id, "intent_id": row.id,
                                      "attempt": row.execution_attempt, "work_order_version": work.version}))
        elif row.operation == INGEST:
            job = self.session.get(KnowledgeIngestionJob, row.resource_id, with_for_update=True)
            if job and job.status != "SUCCEEDED":
                job.status, job.error_code, job.finished_at = "FAILED", code, now
        elif row.operation == DELIVER:
            delivery = self.session.get(NotificationDelivery, row.resource_id, with_for_update=True)
            if delivery and delivery.status != "SUCCEEDED":
                delivery.status, delivery.last_error_code, delivery.next_retry_at = "FAILED", code, None

    def _authorize_manager(self, row, actor):
        from app.core.errors import AppError
        from app.domain.iam import OrganizationScope, ScopeType
        from app.domain.ai import AI_ANALYSIS_REVIEW
        from app.domain.knowledge import KNOWLEDGE_MANAGE
        from app.domain.property import PROPERTY_MANAGE
        from app.repository.identity import IdentityRepository
        from app.service.auth import AuthService
        if row is None:
            raise AppError(code="TASK.NOT_FOUND", message="Task not found", status_code=404)
        current = AuthService(self.session, IdentityRepository(self.session)).load_principal(actor.user_id)
        scope = OrganizationScope(ScopeType.PROPERTY if row.property_id else ScopeType.TENANT,
                                  tenant_id=row.tenant_id, property_id=row.property_id)
        permission = {ANALYZE: AI_ANALYSIS_REVIEW, INGEST: KNOWLEDGE_MANAGE, DELIVER: PROPERTY_MANAGE}[row.operation]
        if current.status != "ACTIVE" or not current.has_permission(permission, scope):
            raise AppError(code="TASK.NOT_FOUND", message="Task not found", status_code=404)
        return current, scope

    @staticmethod
    def metadata(row):
        # No contact/text, credential, lease token or arbitrary task payload.
        return {key: getattr(row, key) for key in (
            "id", "operation", "resource_id", "resource_version", "generation", "status",
            "publish_attempt", "execution_attempt", "last_error_code", "finished_at")}

    def inspect_managed(self, intent_id, actor):
        with begin_owned_transaction(self.session):
            row = self.repo.get(intent_id)
            self._authorize_manager(row, actor)
            return self.metadata(row)

    def retry_managed(self, intent_id, *, actor, reason_code, request_id):
        """Explicit scoped takeover only; immutable old generation and finite new budget."""
        from app.core.errors import AppError
        from app.model.work_order import WorkOrder
        from app.model.knowledge import KnowledgeIngestionJob, KnowledgeDocument, NotificationDelivery
        from app.service.audit import AuditService
        from app.domain.audit import AuditAction, AuditResourceType
        if reason_code not in {"DEPENDENCY_RECOVERED", "OPERATOR_VERIFIED"}:
            raise AppError(code="TASK.INVALID_INPUT", message="Use a supported recovery reason", status_code=422)
        with begin_owned_transaction(self.session):
            row = self._locked(intent_id)
            current, scope = self._authorize_manager(row, actor)
            if row.status != "FAILED":
                raise AppError(code="TASK.CONFLICT", message="Only a failed task can be explicitly recovered", status_code=409)
            # Lock source generation first: concurrent/repeated requests create one successor.
            successor = self.session.scalar(select(TaskIntent).where(TaskIntent.operation == row.operation,
                TaskIntent.resource_id == row.resource_id, TaskIntent.generation == row.generation + 1)
                .order_by(TaskIntent.id).with_for_update().execution_options(populate_existing=True))
            if successor is not None:
                return self.metadata(successor)
            version = row.resource_version
            if row.operation == ANALYZE:
                from app.repository.work_order import WorkOrderRepository
                work = WorkOrderRepository(self.session).get_work_order_for_update(row.resource_id)
                if (work is None or work.status not in {"PENDING_ANALYSIS", "PENDING_ASSIGNMENT"}
                        or work.current_assignment_id is not None or work.version not in {version, version + 1}):
                    raise AppError(code="TASK.CONFLICT", message="Work order changed; use its current business action", status_code=409)
                version = work.version
                # Any newer business intent must win over old administrative retries.
                newer = self.session.scalar(select(TaskIntent.id).where(TaskIntent.operation == ANALYZE,
                    TaskIntent.resource_id == work.id, TaskIntent.id > row.id).limit(1).with_for_update())
                if newer is not None:
                    raise AppError(code="TASK.CONFLICT", message="A newer task already owns this work order", status_code=409)
            elif row.operation == INGEST:
                job = self.session.get(KnowledgeIngestionJob, row.resource_id, with_for_update=True, populate_existing=True)
                document = self.session.get(KnowledgeDocument, job.document_id, with_for_update=True) if job else None
                if (job is None or job.status == "SUCCEEDED" or document is None or document.status == "ARCHIVED"
                        or (document.current_version is not None and document.current_version > job.version)):
                    raise AppError(code="TASK.CONFLICT", message="Knowledge version no longer eligible", status_code=409)
            else:
                delivery = self.session.get(NotificationDelivery, row.resource_id, with_for_update=True, populate_existing=True)
                if delivery is None or delivery.status != "FAILED":
                    raise AppError(code="TASK.CONFLICT", message="Delivery no longer failed", status_code=409)
                # Deliberate operator request, never an automatic counter reset.
                delivery.status, delivery.next_retry_at, delivery.last_error_code = "PENDING", self.clock(), None
            new = self.repo.add(TaskIntent(tenant_id=row.tenant_id, property_id=row.property_id,
                operation=row.operation, resource_id=row.resource_id, resource_version=version,
                generation=row.generation + 1, request_id=request_id, task_id=str(uuid4()),
                status="PENDING", publish_attempt=0, execution_attempt=0, available_at=self.clock()))
            AuditService(self.session).append(action=AuditAction.TASK_INTENT_RETRIED,
                resource_type=AuditResourceType.TASK_INTENT, resource_id=new.id,
                actor_user_id=current.user_id, scope_type=scope.scope_type, tenant_id=row.tenant_id,
                property_id=row.property_id, request_id=request_id,
                details={"previous_id":row.id,"generation":new.generation,"reason_code":reason_code})
            return self.metadata(new)

    def complete_analysis_managed(self, intent_id, *, actor, reason_code, request_id):
        """Create one audited successor for a completed but attachment-incomplete analysis."""
        from app.core.errors import AppError
        from app.model.work_order import WorkOrderLog
        from app.repository.ai import AiRepository
        from app.repository.work_order import WorkOrderRepository
        from app.service.audit import AuditService
        from app.domain.audit import AuditAction, AuditResourceType

        if reason_code != "ATTACHMENT_COMPLETION":
            raise AppError(
                code="TASK.INVALID_INPUT",
                message="Use the attachment completion reason",
                status_code=422,
        )
        with begin_owned_transaction(self.session):
            row = self._locked(intent_id)
            if row is None or row.operation != ANALYZE or row.status != "SUCCEEDED":
                raise AppError(
                    code="TASK.CONFLICT",
                    message="Only a succeeded analysis task can be completed",
                    status_code=409,
                )
            current, scope = self._authorize_manager(row, actor)
            # A repeated authenticated request for the same immutable source
            # returns its one successor even if that successor has already
            # started or completed the business operation.
            successor = self.session.scalar(
                select(TaskIntent)
                .where(
                    TaskIntent.operation == ANALYZE,
                    TaskIntent.resource_id == row.resource_id,
                    TaskIntent.generation == row.generation + 1,
                )
                .order_by(TaskIntent.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if successor is not None:
                return self.metadata(successor)
            work_repo = WorkOrderRepository(self.session)
            work = work_repo.get_work_order_for_update(row.resource_id)
            if (
                work is None
                or work.status != "PENDING_ASSIGNMENT"
                or work.current_assignment_id is not None
                or work_repo.get_active_assignment(work.id) is not None
                or work.version not in {row.resource_version, row.resource_version + 1}
            ):
                raise AppError(
                    code="TASK.CONFLICT",
                    message="Work order changed; attachment completion is no longer safe",
                    status_code=409,
                )
            ai_repo = AiRepository(self.session)
            analysis = ai_repo.get_current_analysis_for_update(work.id)
            if analysis is None or analysis.status not in {"COMPLETED", "LOW_CONFIDENCE"}:
                raise AppError(
                    code="TASK.CONFLICT",
                    message="The current analysis is not eligible for completion",
                    status_code=409,
                )
            if work_repo.attachment_completion_result(work.id, analysis.id) is not None:
                raise AppError(
                    code="TASK.CONFLICT",
                    message="The accepted attachments are already complete",
                    status_code=409,
                )
            if any(item.eligible for item in ai_repo.list_recommendations(work.id)):
                raise AppError(
                    code="TASK.CONFLICT",
                    message="An eligible recommendation must be resolved before attachment completion",
                    status_code=409,
                )
            media = ai_repo.list_media_with_files(work.id)
            accepted = tuple(
                (item, file_object)
                for item, file_object in media
                if item.taken_phase == "REPORT" and item.kind in {"IMAGE", "AUDIO"}
            )
            if not accepted:
                raise AppError(
                    code="TASK.CONFLICT",
                    message="No accepted report attachments require completion",
                    status_code=409,
                )
            newer = self.session.scalar(
                select(TaskIntent.id)
                .where(
                    TaskIntent.operation == ANALYZE,
                    TaskIntent.resource_id == work.id,
                    TaskIntent.id > row.id,
                )
                .limit(1)
                .with_for_update()
            )
            if newer is not None:
                raise AppError(
                    code="TASK.CONFLICT",
                    message="A newer task already owns this work order",
                    status_code=409,
                )
            new = self.repo.add(TaskIntent(
                tenant_id=row.tenant_id,
                property_id=row.property_id,
                operation=ANALYZE,
                resource_id=work.id,
                resource_version=work.version,
                generation=row.generation + 1,
                request_id=request_id,
                task_id=str(uuid4()),
                status="PENDING",
                publish_attempt=0,
                execution_attempt=0,
                available_at=self.clock(),
            ))
            work_repo.add_log(WorkOrderLog(
                tenant_id=work.tenant_id,
                property_id=work.property_id,
                work_order_id=work.id,
                actor_user_id=current.user_id,
                actor_type="PROPERTY",
                from_status=work.status,
                to_status=work.status,
                process_reason=work.process_reason,
                event_code="AI_ATTACHMENT_COMPLETION_REQUESTED",
                request_id=request_id,
                payload_json={
                    "task_id": new.task_id,
                    "intent_id": new.id,
                    "source_intent_id": row.id,
                    "source_analysis_id": analysis.id,
                    "generation": new.generation,
                    "attachment_count": len(accepted),
                    "reason_code": reason_code,
                },
            ))
            AuditService(self.session).append(
                action=AuditAction.TASK_INTENT_RETRIED,
                resource_type=AuditResourceType.TASK_INTENT,
                resource_id=new.id,
                actor_user_id=current.user_id,
                scope_type=scope.scope_type,
                tenant_id=row.tenant_id,
                property_id=row.property_id,
                request_id=request_id,
                details={
                    "previous_id": row.id,
                    "generation": new.generation,
                    "reason_code": reason_code,
                    "source_analysis_id": analysis.id,
                    "attachment_count": len(accepted),
                },
            )
            return self.metadata(new)
