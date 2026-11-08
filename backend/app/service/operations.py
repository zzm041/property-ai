"""Tenant/property-scoped current operations statistics."""

from collections.abc import Callable, Iterable, Sequence
from datetime import date, datetime

from sqlalchemy.orm import Session

from app.core.errors import AppError, IamErrorCode, StatsErrorCode
from app.core.time import to_business_date, utc_now
from app.domain.iam import OrganizationScope, Principal, ScopeType
from app.domain.incident import IncidentStatus
from app.domain.operations import STATS_READ
from app.domain.work_order import (
    TERMINAL_STATUSES,
    SlaClockState,
    WorkOrderCategory,
    WorkOrderPriority,
    WorkOrderStatus,
)
from app.model.property import Property
from app.repository.operations import GroupedCountRow, OperationsRepository
from app.repository.property import PropertyRepository
from app.schema.operations import (
    CountData,
    IncidentStatsData,
    OperationsOverviewData,
    PropertyOperationsData,
    SlaClockStatsData,
    StaffingStatsData,
    WorkOrderStatsData,
)
from app.service.rbac import RbacService


class OperationsService:
    def __init__(
        self,
        session: Session,
        *,
        repository: OperationsRepository | None = None,
        property_repository: PropertyRepository | None = None,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._repository = repository or OperationsRepository(session)
        self._properties = property_repository or PropertyRepository(session)
        self._clock = clock

    def get_overview(
        self,
        *,
        actor: Principal,
        scope_type: str,
        tenant_id: int,
        property_id: int | None,
    ) -> OperationsOverviewData:
        if isinstance(tenant_id, bool) or tenant_id < 1:
            self._raise_invalid()
        tenant = self._properties.get_tenant(tenant_id)
        if tenant is None or tenant.status != "ACTIVE":
            self._raise_forbidden()

        if scope_type == ScopeType.PROPERTY.value:
            if isinstance(property_id, bool) or property_id is None or property_id < 1:
                self._raise_invalid()
            property_record = self._properties.get_property_by_id(property_id)
            if property_record is None or property_record.tenant_id != tenant_id:
                self._raise_forbidden()
            scope = OrganizationScope(
                ScopeType.PROPERTY,
                tenant_id=tenant_id,
                property_id=property_record.id,
            )
            properties: Sequence[Property] = (property_record,)
        elif scope_type == ScopeType.TENANT.value:
            if property_id is not None:
                self._raise_invalid()
            scope = OrganizationScope(ScopeType.TENANT, tenant_id=tenant_id)
            properties = ()
        else:
            self._raise_invalid()

        RbacService.require_permission(actor, STATS_READ, scope)
        if scope.scope_type is ScopeType.TENANT:
            properties = self._properties.list_all_properties(tenant_id=tenant_id)
        generated_at = self._clock()
        if generated_at.tzinfo is None or generated_at.utcoffset() is None:
            raise ValueError("Operations clock must return a timezone-aware datetime")

        property_ids = tuple(record.id for record in properties)
        business_dates = {
            record.id: to_business_date(generated_at, record.timezone)
            for record in properties
        }
        work_order_status = self._grouped(
            self._repository.list_work_order_status_counts(
                tenant_id=tenant_id,
                property_ids=property_ids,
            )
        )
        work_order_category = self._grouped(
            self._repository.list_work_order_category_counts(
                tenant_id=tenant_id,
                property_ids=property_ids,
            )
        )
        work_order_priority = self._grouped(
            self._repository.list_work_order_priority_counts(
                tenant_id=tenant_id,
                property_ids=property_ids,
            )
        )
        incident_status = self._grouped(
            self._repository.list_incident_status_counts(
                tenant_id=tenant_id,
                property_ids=property_ids,
            )
        )
        sla_state = self._grouped(
            self._repository.list_sla_clock_state_counts(
                tenant_id=tenant_id,
                property_ids=property_ids,
            )
        )
        active_assignments = dict(
            self._repository.list_active_staff_assignment_counts(
                tenant_id=tenant_id,
                property_ids=property_ids,
                at=generated_at,
            )
        )
        current_staffing = {
            row[0]: row[1:]
            for row in self._repository.list_current_staffing(
                tenant_id=tenant_id,
                business_dates=business_dates,
                at=generated_at,
            )
        }

        property_data = tuple(
            self._property_data(
                record=record,
                business_date=business_dates[record.id],
                work_order_status=work_order_status.get(record.id, {}),
                incident_status=incident_status.get(record.id, {}),
                sla_state=sla_state.get(record.id, {}),
                active_assignments=active_assignments.get(record.id, 0),
                staffing=current_staffing.get(record.id, (0, 0, 0, 0, 0)),
            )
            for record in properties
        )

        overall_status = self._sum_groups(work_order_status, property_ids)
        overall_category = self._sum_groups(work_order_category, property_ids)
        overall_priority = self._sum_groups(work_order_priority, property_ids)
        overall_incidents = self._sum_groups(incident_status, property_ids)
        overall_sla = self._sum_groups(sla_state, property_ids)
        staffing_totals = tuple(
            sum(current_staffing.get(item, (0, 0, 0, 0, 0))[index] for item in property_ids)
            for index in range(5)
        )
        terminal_codes = {status.value for status in TERMINAL_STATUSES}
        work_order_total = sum(overall_status.values())
        terminal_total = sum(overall_status.get(code, 0) for code in terminal_codes)
        active_incident_codes = {
            IncidentStatus.OPEN.value,
            IncidentStatus.MONITORING.value,
        }

        return OperationsOverviewData(
            scope_type=scope.scope_type.value,
            tenant_id=tenant_id,
            property_id=property_id,
            generated_at=generated_at,
            property_count=len(properties),
            work_orders=WorkOrderStatsData(
                total=work_order_total,
                open=work_order_total - terminal_total,
                terminal=terminal_total,
                by_status=self._counts(WorkOrderStatus, overall_status),
                by_category=self._counts(WorkOrderCategory, overall_category),
                by_priority=self._counts(WorkOrderPriority, overall_priority),
            ),
            incidents=IncidentStatsData(
                total=sum(overall_incidents.values()),
                active=sum(overall_incidents.get(code, 0) for code in active_incident_codes),
                by_status=self._counts(IncidentStatus, overall_incidents),
            ),
            sla_clocks=SlaClockStatsData(
                warning=overall_sla.get(SlaClockState.WARNING.value, 0),
                overdue=overall_sla.get(SlaClockState.OVERDUE.value, 0),
                by_state=self._counts(SlaClockState, overall_sla),
            ),
            staffing=StaffingStatsData(
                active_property_assignments=sum(active_assignments.values()),
                current_status_records=staffing_totals[0],
                unreported_status=max(
                    sum(active_assignments.values()) - staffing_totals[0],
                    0,
                ),
                on_duty=staffing_totals[1],
                available=staffing_totals[2],
                current_load=staffing_totals[3],
                on_duty_capacity=staffing_totals[4],
            ),
            properties=list(property_data),
        )

    @staticmethod
    def _grouped(rows: Sequence[GroupedCountRow]) -> dict[int, dict[str, int]]:
        grouped: dict[int, dict[str, int]] = {}
        for property_id, code, count in rows:
            grouped.setdefault(property_id, {})[code] = count
        return grouped

    @staticmethod
    def _sum_groups(
        grouped: dict[int, dict[str, int]],
        property_ids: Sequence[int],
    ) -> dict[str, int]:
        totals: dict[str, int] = {}
        for property_id in property_ids:
            for code, count in grouped.get(property_id, {}).items():
                totals[code] = totals.get(code, 0) + count
        return totals

    @staticmethod
    def _counts(enum_type: Iterable, counts: dict[str, int]) -> list[CountData]:
        return [CountData(code=item.value, count=counts.get(item.value, 0)) for item in enum_type]

    @staticmethod
    def _property_data(
        *,
        record: Property,
        business_date: date,
        work_order_status: dict[str, int],
        incident_status: dict[str, int],
        sla_state: dict[str, int],
        active_assignments: int,
        staffing: tuple[int, int, int, int, int],
    ) -> PropertyOperationsData:
        terminal_codes = {status.value for status in TERMINAL_STATUSES}
        work_order_total = sum(work_order_status.values())
        terminal_total = sum(work_order_status.get(code, 0) for code in terminal_codes)
        return PropertyOperationsData(
            property_id=record.id,
            property_name=record.name,
            timezone=record.timezone,
            business_date=business_date,
            work_orders_total=work_order_total,
            work_orders_open=work_order_total - terminal_total,
            work_orders_terminal=terminal_total,
            incidents_active=incident_status.get(IncidentStatus.OPEN.value, 0)
            + incident_status.get(IncidentStatus.MONITORING.value, 0),
            sla_warning=sla_state.get(SlaClockState.WARNING.value, 0),
            sla_overdue=sla_state.get(SlaClockState.OVERDUE.value, 0),
            active_staff_assignments=active_assignments,
            unreported_status=max(active_assignments - staffing[0], 0),
            on_duty=staffing[1],
            available=staffing[2],
        )

    @staticmethod
    def _raise_invalid() -> None:
        raise AppError(
            code=StatsErrorCode.INVALID_INPUT,
            message="Statistics scope is invalid",
            status_code=422,
        )

    @staticmethod
    def _raise_forbidden() -> None:
        raise AppError(
            code=IamErrorCode.FORBIDDEN,
            message="Permission denied",
            status_code=403,
        )
