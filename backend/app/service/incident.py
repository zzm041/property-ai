"""Incident aggregation by verifiable property/building/category/window rules."""

from collections.abc import Callable
from datetime import datetime, timedelta

from pydantic import ValidationError
from sqlalchemy import event
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.database import begin_owned_transaction, require_owned_transaction
from app.core.errors import AppError, IamErrorCode, IncidentErrorCode
from app.core.time import utc_now
from app.domain.ai import (
    INCIDENT_MANAGE,
    INCIDENT_READ,
    INCIDENT_SUMMARY_PROMPT_VERSION,
    IncidentSummaryOutput,
)
from app.domain.governance import AuditAction, AuditResourceType
from app.domain.identity import OrganizationScope, Principal, ScopeType
from app.domain.governance import IncidentStatus
from app.model.ai import Incident, IncidentWorkOrder
from app.model.employee import Employee
from app.model.work_order import WorkOrder, WorkOrderAssignment
from app.provider.errors import ProviderError
from app.provider.factory import build_llm_provider
from app.provider.llm import LLMProvider
from app.repository.ai import AiRepository
from app.repository.employee import EmployeeRepository
from app.repository.identity import IdentityRepository
from app.repository.work_order import WorkOrderRepository
from app.service.identity.auth import AuthService
from app.service.audit import AuditService
from app.service.identity.rbac import RbacService


class IncidentService:
    def __init__(
        self,
        session: Session,
        *,
        settings: Settings | None = None,
        llm: LLMProvider | None = None,
        clock: Callable[[], datetime] = utc_now,
        principal_loader: Callable[[int], Principal] | None = None,
    ) -> None:
        self._session = session
        self._settings = settings or get_settings()
        self._llm = llm or build_llm_provider(self._settings)
        self._clock = clock
        self._ai = AiRepository(session)
        self._employees = EmployeeRepository(session)
        self._work_orders = WorkOrderRepository(session)
        self._audit = AuditService(session)
        self._principal_loader = principal_loader or AuthService(
            session,
            IdentityRepository(session),
        ).load_principal

    def aggregate_locked(
        self,
        record: WorkOrder,
        *,
        subcategory: str,
        request_id: str,
    ) -> Incident | None:
        require_owned_transaction(self._session)
        property_record = self._work_orders.get_property_for_update(record.property_id)
        if (
            property_record is None
            or property_record.tenant_id != record.tenant_id
        ):
            return None
        window = timedelta(minutes=self._settings.incident_window_minutes)
        building_id = self._ai.get_building_id(record.house_id)
        cluster = self._ai.list_cluster_work_orders(
            tenant_id=record.tenant_id,
            property_id=record.property_id,
            building_id=building_id,
            category=record.category,
            subcategory=subcategory,
            since=record.created_at - window,
            until=record.created_at + window,
        )
        if len(cluster) < self._settings.incident_threshold:
            return None
        incident = self._ai.find_open_incident(
            tenant_id=record.tenant_id,
            property_id=record.property_id,
            building_id=building_id,
            category=record.category,
            subcategory=subcategory,
            seen_after=record.created_at - window,
            seen_before=record.created_at + window,
        )
        created = False
        if incident is None:
            primary_work_order = cluster[0]
            primary_assignment = None
            for candidate in cluster:
                candidate_assignment = self._work_orders.get_active_assignment(
                    candidate.id
                )
                if candidate_assignment is None:
                    continue
                primary_work_order = candidate
                primary_assignment = candidate_assignment
                break
            incident = self._ai.add_incident(
                Incident(
                    tenant_id=record.tenant_id,
                    property_id=record.property_id,
                    building_id=building_id,
                    status=IncidentStatus.OPEN.value,
                    category=record.category,
                    subcategory=subcategory,
                    summary=None,
                    first_seen_at=min(item.created_at for item in cluster),
                    last_seen_at=max(item.created_at for item in cluster),
                    work_order_count=0,
                    primary_work_order_id=primary_work_order.id,
                    primary_employee_id=(
                        None
                        if primary_assignment is None
                        else primary_assignment.employee_id
                    ),
                )
            )
            created = True
            if primary_assignment is not None:
                self._work_orders.mark_auto_incident_capacity_owner(
                    primary_assignment.id,
                    incident_id=incident.id,
                )
        for item in cluster:
            if item.property_id != incident.property_id or item.tenant_id != incident.tenant_id:
                continue
            existing = self._ai.get_incident_link(item.id)
            if existing is not None:
                continue
            self._ai.add_incident_link(
                IncidentWorkOrder(
                    tenant_id=incident.tenant_id,
                    property_id=incident.property_id,
                    incident_id=incident.id,
                    work_order_id=item.id,
                )
            )
            item.incident_id = incident.id
            self._audit.append(
                action=AuditAction.WORK_ORDER_LINKED_TO_INCIDENT,
                resource_type=AuditResourceType.INCIDENT,
                actor_user_id=None,
                resource_id=incident.id,
                request_id=request_id,
                scope_type=ScopeType.PROPERTY,
                tenant_id=incident.tenant_id,
                property_id=incident.property_id,
                details={
                    "incident_id": incident.id,
                    "work_order_id": item.id,
                },
            )
            if (
                item.id != record.id
                and item.status == "PENDING_ASSIGNMENT"
                and item.current_assignment_id is None
            ):
                if self._settings.durable_tasks_enabled:
                    from app.service.task_intent import ANALYZE, register_intent
                    register_intent(self._session, operation=ANALYZE, resource=item,
                                    request_id=f"incident:{incident.id}:redispatch:{item.id}")
                else:
                    pending = self._session.info.setdefault("pending_incident_redispatch", {})
                    pending[item.id] = f"incident:{incident.id}:redispatch:{item.id}"
                    self._session.info["incident_redispatch_settings"] = self._settings
        links = self._ai.list_incident_work_orders(incident.id)
        incident.work_order_count = len(links)
        incident.first_seen_at = min(
            incident.first_seen_at,
            *(item.created_at for item in cluster),
        )
        incident.last_seen_at = max(
            incident.last_seen_at,
            *(item.created_at for item in cluster),
        )
        if incident.primary_work_order_id is None and cluster:
            incident.primary_work_order_id = cluster[0].id
        if incident.primary_employee_id is None and incident.primary_work_order_id:
            primary_assignment = self._work_orders.get_active_assignment(
                incident.primary_work_order_id
            )
            if primary_assignment is not None:
                incident.primary_employee_id = primary_assignment.employee_id
                self._work_orders.mark_auto_incident_capacity_owner(
                    primary_assignment.id,
                    incident_id=incident.id,
                )
        action = (
            AuditAction.INCIDENT_CREATED if created else AuditAction.INCIDENT_UPDATED
        )
        self._audit.append(
            action=action,
            resource_type=AuditResourceType.INCIDENT,
            actor_user_id=None,
            resource_id=incident.id,
            request_id=request_id,
            scope_type=ScopeType.PROPERTY,
            tenant_id=incident.tenant_id,
            property_id=incident.property_id,
            details={
                "incident_id": incident.id,
                "category": incident.category,
                "work_order_count": incident.work_order_count,
            },
        )
        return incident

    def list_incidents(
        self,
        *,
        actor: Principal,
        property_id: int,
        tenant_id: int | None = None,
        status: str | None = None,
        after_id: int = 0,
        limit: int = 50,
    ) -> tuple[Incident, ...]:
        property_record = self._work_orders.get_property(property_id)
        if property_record is None:
            raise AppError(
                code=IncidentErrorCode.NOT_FOUND,
                message="Incident was not found",
                status_code=404,
            )
        if tenant_id is not None and tenant_id != property_record.tenant_id:
            raise AppError(
                code=IncidentErrorCode.SCOPE_MISMATCH,
                message="Declared scope does not match the property",
                status_code=422,
            )
        scope = OrganizationScope(
            ScopeType.PROPERTY,
            tenant_id=property_record.tenant_id,
            property_id=property_record.id,
        )
        RbacService.require_permission(actor, INCIDENT_READ, scope)
        if not actor.has_permission(INCIDENT_MANAGE, scope):
            employee = self._eligible_participant(actor, property_record.tenant_id, property_record.id)
            if employee is None:
                return ()
            return self._ai.list_incidents_for_participant(
                tenant_id=property_record.tenant_id,
                property_id=property_record.id,
                employee_id=employee.id,
                status=status,
                after_id=after_id,
                limit=limit,
            )
        return self._ai.list_incidents(
            tenant_id=property_record.tenant_id,
            property_id=property_record.id,
            status=status,
            after_id=after_id,
            limit=limit,
        )

    def get_incident(self, *, actor: Principal, incident_id: int) -> Incident:
        incident = self._ai.get_incident(incident_id)
        if incident is None:
            raise AppError(
                code=IncidentErrorCode.NOT_FOUND,
                message="Incident was not found",
                status_code=404,
            )
        scope = OrganizationScope(
            ScopeType.PROPERTY,
            tenant_id=incident.tenant_id,
            property_id=incident.property_id,
        )
        RbacService.require_permission(actor, INCIDENT_READ, scope)
        if not actor.has_permission(INCIDENT_MANAGE, scope):
            employee = self._eligible_participant(actor, incident.tenant_id, incident.property_id)
            if (
                employee is None
                or not self._ai.is_incident_participant(
                    incident_id=incident.id,
                    tenant_id=incident.tenant_id,
                    property_id=incident.property_id,
                    employee_id=employee.id,
                )
            ):
                raise AppError(
                    code=IamErrorCode.FORBIDDEN,
                    message="Permission denied",
                    status_code=403,
                )
        return incident

    def _eligible_participant(self, actor: Principal, tenant_id: int, property_id: int) -> Employee | None:
        employee = self._employees.get_employee_for_user(actor.user_id)
        if employee is None or employee.status != "ACTIVE" or employee.tenant_id != tenant_id:
            return None
        if self._employees.find_effective_assignment(
            employee_id=employee.id, property_id=property_id, now=utc_now(),
        ) is None:
            return None
        return employee

    def update_status(
        self,
        *,
        actor: Principal,
        incident_id: int,
        target_status: str,
        reason: str,
        expected_updated_at: datetime,
        request_id: str,
    ) -> Incident:
        try:
            target = IncidentStatus(target_status)
        except ValueError as exc:
            raise AppError(
                code=IncidentErrorCode.INVALID_INPUT,
                message="Incident status is invalid",
                status_code=422,
            ) from exc
        clean_reason = reason.strip()
        if len(clean_reason) < 2:
            raise AppError(
                code=IncidentErrorCode.INVALID_INPUT,
                message="Incident status reason is required",
                status_code=422,
            )
        transitions = {
            IncidentStatus.OPEN: {
                IncidentStatus.MONITORING,
                IncidentStatus.RESOLVED,
            },
            IncidentStatus.MONITORING: {
                IncidentStatus.OPEN,
                IncidentStatus.RESOLVED,
            },
            IncidentStatus.RESOLVED: {
                IncidentStatus.OPEN,
                IncidentStatus.CLOSED,
            },
            IncidentStatus.CLOSED: {IncidentStatus.OPEN},
        }
        with begin_owned_transaction(self._session):
            current_actor = self._reload_actor(actor)
            incident = self._ai.get_incident_for_update(incident_id)
            if incident is None:
                raise AppError(
                    code=IncidentErrorCode.NOT_FOUND,
                    message="Incident was not found",
                    status_code=404,
                )
            scope = OrganizationScope(
                ScopeType.PROPERTY,
                tenant_id=incident.tenant_id,
                property_id=incident.property_id,
            )
            RbacService.require_permission(current_actor, INCIDENT_MANAGE, scope)
            current = IncidentStatus(incident.status)
            if incident.updated_at != expected_updated_at:
                raise AppError(
                    code=IncidentErrorCode.CONFLICT,
                    message="Incident changed; refresh before updating its status",
                    status_code=409,
                    details={"retryable": True},
                )
            if target not in transitions[current]:
                raise AppError(
                    code=IncidentErrorCode.CONFLICT,
                    message="Incident status transition is not allowed",
                    status_code=409,
                    details={"from_status": current.value, "to_status": target.value},
                )
            incident.status = target.value
            self._audit.append(
                action=AuditAction.INCIDENT_UPDATED,
                resource_type=AuditResourceType.INCIDENT,
                actor_user_id=current_actor.user_id,
                resource_id=incident.id,
                request_id=request_id,
                scope_type=ScopeType.PROPERTY,
                tenant_id=incident.tenant_id,
                property_id=incident.property_id,
                details={
                    "incident_id": incident.id,
                    "category": incident.category,
                    "work_order_count": incident.work_order_count,
                    "from_status": current.value,
                    "to_status": target.value,
                    "reason": clean_reason,
                },
            )
            # Incident disposition deliberately does not mutate member work
            # orders, receipts, assignments, or resident feedback history.
            return incident

    def summarize(
        self,
        *,
        actor: Principal,
        incident_id: int,
        request_id: str,
    ) -> Incident:
        with begin_owned_transaction(self._session):
            current_actor = self._reload_actor(actor)
            incident = self._ai.get_incident_for_update(incident_id)
            if incident is None:
                raise AppError(
                    code=IncidentErrorCode.NOT_FOUND,
                    message="Incident was not found",
                    status_code=404,
                )
            scope = OrganizationScope(
                ScopeType.PROPERTY,
                tenant_id=incident.tenant_id,
                property_id=incident.property_id,
            )
            RbacService.require_permission(current_actor, INCIDENT_MANAGE, scope)
            snapshot = {
                "id": incident.id,
                "tenant_id": incident.tenant_id,
                "property_id": incident.property_id,
                "category": incident.category,
                "count": incident.work_order_count,
                "last_seen_at": incident.last_seen_at,
                "updated_at": incident.updated_at,
            }
        if self._session.in_transaction():
            from app.core.database import TransactionContractError

            raise TransactionContractError(
                "Provider calls must not hold a business transaction"
            )
        try:
            payload = self._llm.structured_completion(
                prompt=f"incident={snapshot['id']};category={snapshot['category']}",
                prompt_version=INCIDENT_SUMMARY_PROMPT_VERSION,
            )
            output = IncidentSummaryOutput.model_validate(payload)
        except ProviderError as exc:
            raise AppError(
                code=exc.error_code,
                message="Incident summary provider is unavailable",
                status_code=503,
            ) from exc
        except (ValidationError, ValueError) as exc:
            from app.core.errors import AiErrorCode

            raise AppError(
                code=AiErrorCode.INVALID_OUTPUT,
                message="Incident summary provider returned invalid output",
                status_code=502,
            ) from exc
        with begin_owned_transaction(self._session):
            incident = self._ai.get_incident_for_update(incident_id)
            if incident is None:
                raise AppError(
                    code=IncidentErrorCode.NOT_FOUND,
                    message="Incident was not found",
                    status_code=404,
                )
            current_actor = self._reload_actor(actor)
            scope = OrganizationScope(
                ScopeType.PROPERTY,
                tenant_id=incident.tenant_id,
                property_id=incident.property_id,
            )
            RbacService.require_permission(current_actor, INCIDENT_MANAGE, scope)
            if (
                incident.tenant_id != snapshot["tenant_id"]
                or incident.property_id != snapshot["property_id"]
                or incident.category != snapshot["category"]
                or incident.work_order_count != snapshot["count"]
                or incident.last_seen_at != snapshot["last_seen_at"]
                or incident.updated_at != snapshot["updated_at"]
            ):
                raise AppError(
                    code=IncidentErrorCode.CONFLICT,
                    message="Incident changed while its summary was generated",
                    status_code=409,
                    details={"retryable": True},
                )
            incident.ai_summary = output.summary
            incident.affected_scope = output.affected_scope
            if incident.summary is None:
                incident.summary = output.summary
            self._audit.append(
                action=AuditAction.INCIDENT_UPDATED,
                resource_type=AuditResourceType.INCIDENT,
                actor_user_id=actor.user_id,
                resource_id=incident.id,
                request_id=request_id,
                scope_type=ScopeType.PROPERTY,
                tenant_id=incident.tenant_id,
                property_id=incident.property_id,
                details={
                    "incident_id": incident.id,
                    "category": incident.category,
                    "work_order_count": incident.work_order_count,
                },
            )
            return incident

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


class IncidentCapacityService:
    """Keep one transferable load charge for AUTO followers of one employee."""

    def __init__(
        self,
        session: Session,
        *,
        ai_repository: AiRepository | None = None,
        work_order_repository: WorkOrderRepository | None = None,
    ) -> None:
        self._ai = ai_repository or AiRepository(session)
        self._work_orders = work_order_repository or WorkOrderRepository(session)

    def adopt_assignment(
        self,
        *,
        work_order: WorkOrder,
        assignment: WorkOrderAssignment,
    ) -> None:
        incident_id = getattr(work_order, "incident_id", None)
        if incident_id is None:
            return
        incident = self._ai.get_incident_for_update(incident_id)
        if (
            incident is None
            or incident.tenant_id != work_order.tenant_id
            or incident.property_id != work_order.property_id
            or incident.primary_employee_id is not None
        ):
            return
        incident.primary_work_order_id = work_order.id
        incident.primary_employee_id = assignment.employee_id
        self._work_orders.mark_auto_incident_capacity_owner(
            assignment.id,
            incident_id=incident.id,
        )

    def should_release_load(
        self,
        *,
        work_order: WorkOrder,
        assignment: WorkOrderAssignment,
    ) -> bool:
        incident_id = getattr(work_order, "incident_id", None)
        if incident_id is None:
            return True
        incident = self._ai.get_incident_for_update(incident_id)
        if (
            incident is None
            or incident.tenant_id != work_order.tenant_id
            or incident.property_id != work_order.property_id
        ):
            return True
        provenance = self._work_orders.get_auto_assignment_provenance(
            assignment.id,
            for_update=True,
        )
        is_primary_owner = (
            incident.primary_work_order_id == work_order.id
            and incident.primary_employee_id == assignment.employee_id
        )
        is_explicit_owner = (
            provenance is not None and provenance.incident_capacity_owner
        )
        is_unowned_follower = (
            provenance is not None
            and provenance.incident_follow
            and not is_explicit_owner
            and not is_primary_owner
        )
        if is_unowned_follower:
            return False
        if not is_primary_owner and not is_explicit_owner:
            return True

        candidates = self._ai.list_active_incident_assignments_for_update(
            incident_id=incident.id,
            exclude_assignment_id=assignment.id,
        )
        same_employee_follower: tuple[WorkOrder, WorkOrderAssignment] | None = None
        for candidate_work_order, candidate_assignment in candidates:
            if candidate_assignment.employee_id != assignment.employee_id:
                continue
            candidate_provenance = (
                self._work_orders.get_auto_assignment_provenance(
                    candidate_assignment.id,
                    for_update=True,
                )
            )
            if (
                candidate_provenance is not None
                and candidate_provenance.incident_follow
                and not candidate_provenance.incident_capacity_owner
            ):
                same_employee_follower = (
                    candidate_work_order,
                    candidate_assignment,
                )
                break
        if same_employee_follower is not None:
            successor_work_order, successor_assignment = same_employee_follower
            transferred = self._work_orders.mark_auto_incident_capacity_owner(
                successor_assignment.id,
                incident_id=incident.id,
            )
            if transferred:
                if is_primary_owner:
                    incident.primary_work_order_id = successor_work_order.id
                    incident.primary_employee_id = successor_assignment.employee_id
                return False

        if is_primary_owner:
            successor = candidates[0] if candidates else None
            if successor is None:
                incident.primary_work_order_id = None
                incident.primary_employee_id = None
            else:
                successor_work_order, successor_assignment = successor
                incident.primary_work_order_id = successor_work_order.id
                incident.primary_employee_id = successor_assignment.employee_id
                self._work_orders.mark_auto_incident_capacity_owner(
                    successor_assignment.id,
                    incident_id=incident.id,
                )
        return True


@event.listens_for(Session, "after_commit")
def _schedule_incident_redispatch(session: Session) -> None:
    # ``after_commit`` also fires when the auto-dispatch SAVEPOINT is released.
    # Keep the intent buffered until the owning business transaction commits.
    if session.in_nested_transaction():
        return
    pending = session.info.pop("pending_incident_redispatch", None)
    if not pending:
        return
    settings = session.info.pop("incident_redispatch_settings", None) or get_settings()
    from app.worker.enqueue import enqueue_celery_task
    from app.worker.tasks import analyze_work_order_task

    for work_order_id, request_id in sorted(pending.items()):
        enqueue_celery_task(
            settings=settings,
            celery_task=analyze_work_order_task,
            args=(int(work_order_id), str(request_id)),
            event="ai_enqueue_failed",
            extra={"work_order_id": int(work_order_id), "incident_redispatch": True},
        )


@event.listens_for(Session, "after_rollback")
def _clear_pending_incident_redispatch(session: Session) -> None:
    if session.in_nested_transaction():
        return
    session.info.pop("pending_incident_redispatch", None)
    session.info.pop("incident_redispatch_settings", None)
