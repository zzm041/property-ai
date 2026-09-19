"""Incident aggregation by verifiable property/building/category/window rules."""

from collections.abc import Callable
from datetime import datetime, timedelta

from pydantic import ValidationError
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
from app.domain.audit import AuditAction, AuditResourceType
from app.domain.iam import OrganizationScope, Principal, ScopeType
from app.domain.incident import IncidentStatus
from app.model.ai import Incident, IncidentWorkOrder
from app.model.work_order import WorkOrder
from app.provider.factory import build_llm_provider
from app.provider.llm import LLMProvider
from app.repository.ai import AiRepository
from app.repository.work_order import WorkOrderRepository
from app.service.audit import AuditService
from app.service.rbac import RbacService


class IncidentService:
    def __init__(
        self,
        session: Session,
        *,
        settings: Settings | None = None,
        llm: LLMProvider | None = None,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._session = session
        self._settings = settings or get_settings()
        self._llm = llm or build_llm_provider(self._settings)
        self._clock = clock
        self._ai = AiRepository(session)
        self._work_orders = WorkOrderRepository(session)
        self._audit = AuditService(session)

    def aggregate_locked(self, record: WorkOrder, *, request_id: str) -> Incident | None:
        require_owned_transaction(self._session)
        window = timedelta(minutes=self._settings.incident_window_minutes)
        building_id = self._ai.get_building_id(record.house_id)
        cluster = self._ai.list_cluster_work_orders(
            tenant_id=record.tenant_id,
            property_id=record.property_id,
            building_id=building_id,
            category=record.category,
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
        )
        created = False
        now = self._clock()
        if incident is None:
            primary_work_order = cluster[0]
            primary_assignment = self._work_orders.get_active_assignment(
                primary_work_order.id
            )
            incident = self._ai.add_incident(
                Incident(
                    tenant_id=record.tenant_id,
                    property_id=record.property_id,
                    building_id=building_id,
                    status=IncidentStatus.OPEN.value,
                    category=record.category,
                    subcategory=None,
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
        links = self._ai.list_incident_work_orders(incident.id)
        incident.work_order_count = len(links)
        incident.last_seen_at = now
        if incident.primary_work_order_id is None and cluster:
            incident.primary_work_order_id = cluster[0].id
        if incident.primary_employee_id is None and incident.primary_work_order_id:
            primary_assignment = self._work_orders.get_active_assignment(
                incident.primary_work_order_id
            )
            if primary_assignment is not None:
                incident.primary_employee_id = primary_assignment.employee_id
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
        return self._ai.list_incidents(
            tenant_id=property_record.tenant_id,
            property_id=property_record.id,
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
        return incident

    def summarize(
        self,
        *,
        actor: Principal,
        incident_id: int,
        request_id: str,
    ) -> Incident:
        with begin_owned_transaction(self._session):
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
            RbacService.require_permission(actor, INCIDENT_MANAGE, scope)
            snapshot = {
                "id": incident.id,
                "category": incident.category,
                "count": incident.work_order_count,
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
        except (ValidationError, ValueError, Exception):
            output = None
        with begin_owned_transaction(self._session):
            incident = self._ai.get_incident_for_update(incident_id)
            if incident is None:
                raise AppError(
                    code=IncidentErrorCode.NOT_FOUND,
                    message="Incident was not found",
                    status_code=404,
                )
            if output is not None:
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
