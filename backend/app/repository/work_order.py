"""Query/add/flush for WorkOrder aggregates. Service owns commits."""

from dataclasses import dataclass
from datetime import datetime
import json

from sqlalchemy import (
    MetaData,
    Table,
    and_,
    case,
    func,
    inspect as sa_inspect,
    or_,
    select,
    update,
)
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.model.property import House, Property
from app.model.employee import Employee, EmployeeProperty, EmployeeDailyStatus, AttendanceRecord
from app.model.identity import Permission, RolePermission, User, UserRole
from app.model.work_order import (
    FileObject,
    IdempotencyRecord,
    SlaClock,
    SlaPolicy,
    WorkOrder,
    WorkOrderAssignment,
    WorkOrderContent,
    WorkOrderFeedback,
    WorkOrderLog,
    WorkOrderMedia,
    WorkOrderProcess,
    WorkOrderReceipt,
)


@dataclass(frozen=True, slots=True)
class AutoAssignmentProvenance:
    incident_id: int | None
    incident_follow: bool
    incident_capacity_owner: bool


class WorkOrderRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_work_order(self, work_order_id: int) -> WorkOrder | None:
        return self._session.get(WorkOrder, work_order_id)

    def get_work_order_for_update(self, work_order_id: int) -> WorkOrder | None:
        return self._session.scalar(
            select(WorkOrder)
            .where(WorkOrder.id == work_order_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    def get_house(self, house_id: int) -> House | None:
        return self._session.get(House, house_id)

    def get_property(self, property_id: int) -> Property | None:
        return self._session.get(Property, property_id)

    def get_display_context(self, record: WorkOrder) -> dict[str, object]:
        """Only the authorized order's scoped participants, never a user directory."""
        timezone_name = self._session.scalar(
            select(Property.timezone).where(
                Property.id == record.property_id, Property.tenant_id == record.tenant_id
            )
        )

        if timezone_name is None:
            return {"property_timezone": "UTC", "assignee_names": {}}
        participants = select(WorkOrderAssignment.employee_id).where(
            WorkOrderAssignment.work_order_id == record.id,
            WorkOrderAssignment.tenant_id == record.tenant_id,
            WorkOrderAssignment.property_id == record.property_id,
        ).union(select(WorkOrderProcess.assignee_id).where(
            WorkOrderProcess.work_order_id == record.id,
            WorkOrderProcess.tenant_id == record.tenant_id,
            WorkOrderProcess.property_id == record.property_id,
        ))
        names = self._session.execute(
            select(Employee.id, User.display_name)
            .join(User, User.id == Employee.user_id)
            .where(Employee.tenant_id == record.tenant_id, Employee.id.in_(participants))
        ).all()
        return {"property_timezone": timezone_name, "assignee_names": {
            employee_id: name for employee_id, name in names if name and name.strip()
        }}

    def latest_analysis_attempt(self, work_order_id: int) -> WorkOrderLog | None:
        """Call under the work-order mutex; current read, never an RR snapshot."""
        return self._session.scalar(select(WorkOrderLog).where(
            WorkOrderLog.work_order_id == work_order_id,
            WorkOrderLog.event_code == "AI_TASK_ATTEMPT",
        ).order_by(WorkOrderLog.id.desc()).limit(1).with_for_update()
            .execution_options(populate_existing=True))

    def get_property_for_update(self, property_id: int) -> Property | None:
        return self._session.scalar(
            select(Property).where(Property.id == property_id).with_for_update()
        )

    def get_file(self, file_id: int) -> FileObject | None:
        return self._session.get(FileObject, file_id)

    def get_file_for_update(self, file_id: int) -> FileObject | None:
        return self._session.scalar(
            select(FileObject).where(FileObject.id == file_id).with_for_update()
        )

    def get_idempotency(
        self,
        *,
        user_id: int,
        idempotency_key: str,
    ) -> IdempotencyRecord | None:
        return self._session.scalar(
            select(IdempotencyRecord).where(
                IdempotencyRecord.user_id == user_id,
                IdempotencyRecord.idempotency_key == idempotency_key,
            )
        )

    def find_sla_policy(
        self,
        *,
        tenant_id: int,
        property_id: int,
        category: str,
        priority: str,
    ) -> SlaPolicy | None:
        property_level = self._session.scalar(
            select(SlaPolicy).where(
                SlaPolicy.tenant_id == tenant_id,
                SlaPolicy.property_id == property_id,
                SlaPolicy.category == category,
                SlaPolicy.priority == priority,
                SlaPolicy.status == "ACTIVE",
            )
        )
        if property_level is not None:
            return property_level
        return self._session.scalar(
            select(SlaPolicy).where(
                SlaPolicy.tenant_id == tenant_id,
                SlaPolicy.property_id.is_(None),
                SlaPolicy.category == category,
                SlaPolicy.priority == priority,
                SlaPolicy.status == "ACTIVE",
            )
        )

    def get_active_assignment(
        self,
        work_order_id: int,
    ) -> WorkOrderAssignment | None:
        return self._session.scalar(
            select(WorkOrderAssignment).where(
                WorkOrderAssignment.work_order_id == work_order_id,
                WorkOrderAssignment.status == "ACTIVE",
                WorkOrderAssignment.ended_at.is_(None),
            )
        )

    def get_active_assignment_for_update(
        self,
        work_order_id: int,
    ) -> WorkOrderAssignment | None:
        return self._session.scalar(
            select(WorkOrderAssignment)
            .where(
                WorkOrderAssignment.work_order_id == work_order_id,
                WorkOrderAssignment.status == "ACTIVE",
                WorkOrderAssignment.ended_at.is_(None),
            )
            .with_for_update()
        )

    def count_active_assignments_for_employee(
        self,
        *,
        employee_id: int,
        property_id: int,
    ) -> int:
        return int(
            self._session.scalar(
                select(func.count())
                .select_from(WorkOrderAssignment)
                .where(
                    WorkOrderAssignment.employee_id == employee_id,
                    WorkOrderAssignment.property_id == property_id,
                    WorkOrderAssignment.status == "ACTIVE",
                    WorkOrderAssignment.ended_at.is_(None),
                )
            )
            or 0
        )

    def list_assignments(self, work_order_id: int) -> tuple[WorkOrderAssignment, ...]:
        return tuple(
            self._session.scalars(
                select(WorkOrderAssignment)
                .where(WorkOrderAssignment.work_order_id == work_order_id)
                .order_by(WorkOrderAssignment.id)
            ).all()
        )

    def list_unavailable_unaccepted(self, *, now: datetime, limit: int = 100) -> tuple[int, ...]:
        from app.core.time import current_business_date

        properties = self._session.execute(
            select(Property.id, Property.timezone).join(WorkOrder, WorkOrder.property_id == Property.id)
            .where(WorkOrder.status == "ASSIGNED").distinct()
        ).all()
        if not properties:
            return ()
        # Same zoneinfo policy as attendance; no MySQL timezone-table dependency.
        today = case(*[(WorkOrder.property_id == property_id,
                        current_business_date(zone, clock=lambda: now))
                       for property_id, zone in properties])
        effective = select(EmployeeProperty.id).where(
            EmployeeProperty.employee_id == Employee.id,
            EmployeeProperty.tenant_id == WorkOrder.tenant_id,
            EmployeeProperty.property_id == WorkOrder.property_id,
            EmployeeProperty.status == "ACTIVE", EmployeeProperty.valid_from <= now,
            or_(EmployeeProperty.valid_to.is_(None), EmployeeProperty.valid_to > now),
        ).correlate(Employee, WorkOrder).exists()
        on_duty = select(AttendanceRecord.id).join(EmployeeDailyStatus, and_(
            EmployeeDailyStatus.employee_id == AttendanceRecord.employee_id,
            EmployeeDailyStatus.property_id == AttendanceRecord.property_id,
            EmployeeDailyStatus.business_date == AttendanceRecord.business_date,
        )).where(
            AttendanceRecord.employee_id == Employee.id,
            AttendanceRecord.property_id == WorkOrder.property_id,
            AttendanceRecord.check_out_at.is_(None),
            EmployeeDailyStatus.business_date == today,
            EmployeeDailyStatus.on_duty == "ON_DUTY",
        ).correlate(Employee, WorkOrder).exists()
        return tuple(self._session.scalars(
            select(WorkOrder.id).join(WorkOrderAssignment, WorkOrderAssignment.id == WorkOrder.current_assignment_id)
            .join(Employee, Employee.id == WorkOrderAssignment.employee_id)
            .join(User, User.id == Employee.user_id)
            .where(WorkOrder.status == "ASSIGNED", WorkOrderAssignment.status == "ACTIVE",
                   WorkOrderAssignment.ended_at.is_(None),
                   or_(Employee.status != "ACTIVE", User.status != "ACTIVE", ~effective, ~on_duty))
            .order_by(WorkOrder.id).limit(min(max(limit, 1), 100))
        ).all())

    def get_content(self, work_order_id: int) -> WorkOrderContent | None:
        return self._session.scalar(
            select(WorkOrderContent).where(
                WorkOrderContent.work_order_id == work_order_id
            )
        )

    def list_media(self, work_order_id: int) -> tuple[WorkOrderMedia, ...]:
        return tuple(
            self._session.scalars(
                select(WorkOrderMedia)
                .where(WorkOrderMedia.work_order_id == work_order_id)
                .order_by(WorkOrderMedia.id)
            ).all()
        )

    def list_logs(self, work_order_id: int) -> tuple[WorkOrderLog, ...]:
        return tuple(
            self._session.scalars(
                select(WorkOrderLog)
                .where(WorkOrderLog.work_order_id == work_order_id)
                .order_by(WorkOrderLog.id)
            ).all()
        )

    def list_processes(self, work_order_id: int) -> tuple[WorkOrderProcess, ...]:
        return tuple(
            self._session.scalars(
                select(WorkOrderProcess)
                .where(WorkOrderProcess.work_order_id == work_order_id)
                .order_by(WorkOrderProcess.cycle_no)
            ).all()
        )

    def get_current_process(
        self,
        work_order_id: int,
    ) -> WorkOrderProcess | None:
        return self._session.scalar(
            select(WorkOrderProcess)
            .where(WorkOrderProcess.work_order_id == work_order_id)
            .order_by(WorkOrderProcess.cycle_no.desc())
            .limit(1)
        )

    def get_receipt_for_process(self, process_id: int) -> WorkOrderReceipt | None:
        return self._session.scalar(
            select(WorkOrderReceipt).where(WorkOrderReceipt.process_id == process_id)
        )

    def employee_global_occupancy(self, *, employee_id: int, tenant_id: int, lock: bool = False, collapse_incidents: bool = False) -> int:
        """Active reservations, including unaccepted and WAIT_CONFIRM, across properties.

        Caller holds Employee FOR UPDATE when lock=True. A locking current read
        avoids a stale REPEATABLE READ snapshot after waiting for that mutex.
        """
        query = select(WorkOrderAssignment.id).where(
            WorkOrderAssignment.employee_id == employee_id,
            WorkOrderAssignment.tenant_id == tenant_id,
            WorkOrderAssignment.status == "ACTIVE",
            WorkOrderAssignment.ended_at.is_(None),
        ).order_by(WorkOrderAssignment.id)
        if lock:
            query = query.with_for_update()
        ids = self._session.scalars(query).all()
        if not collapse_incidents:
            return len(ids)
        units = set()
        for assignment_id in ids:
            provenance = self.get_auto_assignment_provenance(assignment_id, for_update=lock)
            if provenance is not None and (provenance.incident_follow or provenance.incident_capacity_owner):
                units.add(("incident", provenance.incident_id))
            else:
                units.add(("assignment", assignment_id))
        return len(units)

    def employee_last_assignment_id(self, *, employee_id: int, tenant_id: int) -> int:
        return int(self._session.scalar(
            select(WorkOrderAssignment.id).where(
                WorkOrderAssignment.employee_id == employee_id,
                WorkOrderAssignment.tenant_id == tenant_id,
            ).order_by(WorkOrderAssignment.id.desc()).limit(1)
        ) or 0)

    def list_sla_policies_for_property(
        self,
        *,
        tenant_id: int,
        property_id: int,
    ) -> tuple[SlaPolicy, ...]:
        return tuple(
            self._session.scalars(
                select(SlaPolicy)
                .where(
                    SlaPolicy.tenant_id == tenant_id,
                    or_(
                        SlaPolicy.property_id == property_id,
                        SlaPolicy.property_id.is_(None),
                    ),
                )
                .order_by(
                    SlaPolicy.category,
                    SlaPolicy.priority,
                    SlaPolicy.property_scope_key,
                )
            ).all()
        )

    def get_sla_policy_for_update(
        self,
        *,
        tenant_id: int,
        property_id: int | None,
        category: str,
        priority: str,
    ) -> SlaPolicy | None:
        property_clause = (
            SlaPolicy.property_id.is_(None)
            if property_id is None
            else SlaPolicy.property_id == property_id
        )
        return self._session.scalar(
            select(SlaPolicy)
            .where(
                SlaPolicy.tenant_id == tenant_id,
                property_clause,
                SlaPolicy.category == category,
                SlaPolicy.priority == priority,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
            .execution_options(populate_existing=True)
        )

    def get_content_for_update(self, work_order_id: int) -> WorkOrderContent | None:
        return self._session.scalar(
            select(WorkOrderContent)
            .where(WorkOrderContent.work_order_id == work_order_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    def _assignment_extension_table(self) -> Table | None:
        bind = self._session.get_bind()
        if bind is None:
            return None
        try:
            columns = {
                column["name"]
                for column in sa_inspect(bind).get_columns("work_order_assignments")
            }
            if not {"assigned_source", "payload_json"} <= columns:
                return None
            return Table("work_order_assignments", MetaData(), autoload_with=bind)
        except SQLAlchemyError:
            return None

    @staticmethod
    def _payload_object(value: object) -> dict[str, object]:
        if isinstance(value, dict):
            return dict(value)
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError:
                return {}
            if isinstance(parsed, dict):
                return parsed
        return {}

    def get_auto_assignment_provenance(
        self,
        assignment_id: int,
        *,
        for_update: bool = False,
    ) -> AutoAssignmentProvenance | None:
        """Read 0009 AUTO provenance without mapping it onto shared 0007."""

        table = self._assignment_extension_table()
        if table is None:
            return None
        statement = select(table.c.assigned_source, table.c.payload_json).where(
            table.c.id == assignment_id
        )
        if for_update:
            statement = statement.with_for_update()
        try:
            row = self._session.execute(statement).mappings().one_or_none()
        except SQLAlchemyError:
            return None
        if row is None or row["assigned_source"] != "AUTO":
            return None
        payload = self._payload_object(row["payload_json"])
        raw_incident_id = payload.get("incident_id")
        try:
            incident_id = (
                None if raw_incident_id is None else int(raw_incident_id)
            )
        except (TypeError, ValueError):
            incident_id = None
        return AutoAssignmentProvenance(
            incident_id=incident_id,
            incident_follow=payload.get("incident_follow") is True,
            incident_capacity_owner=payload.get("incident_capacity_owner") is True,
        )

    def mark_auto_incident_capacity_owner(
        self,
        assignment_id: int,
        *,
        incident_id: int,
    ) -> bool:
        table = self._assignment_extension_table()
        if table is None:
            return False
        try:
            row = self._session.execute(
                select(table.c.assigned_source, table.c.payload_json)
                .where(table.c.id == assignment_id)
                .with_for_update()
            ).mappings().one_or_none()
            if row is None or row["assigned_source"] != "AUTO":
                return False
            payload = self._payload_object(row["payload_json"])
            payload["incident_id"] = incident_id
            payload["incident_capacity_owner"] = True
            self._session.execute(
                update(table)
                .where(table.c.id == assignment_id)
                .values(payload_json=payload)
            )
        except SQLAlchemyError:
            return False
        return True

    def is_auto_incident_follow_assignment(self, assignment_id: int) -> bool:
        provenance = self.get_auto_assignment_provenance(assignment_id)
        return provenance is not None and provenance.incident_follow

    def list_work_orders_for_file(self, file_id: int) -> tuple[WorkOrder, ...]:
        return tuple(
            self._session.scalars(
                select(WorkOrder)
                .join(WorkOrderMedia, WorkOrderMedia.work_order_id == WorkOrder.id)
                .where(WorkOrderMedia.file_id == file_id)
                .order_by(WorkOrder.id)
            ).all()
        )

    def list_receipts(self, work_order_id: int) -> tuple[WorkOrderReceipt, ...]:
        return tuple(
            self._session.scalars(
                select(WorkOrderReceipt)
                .where(WorkOrderReceipt.work_order_id == work_order_id)
                .order_by(WorkOrderReceipt.id)
            ).all()
        )

    def list_feedbacks(self, work_order_id: int) -> tuple[WorkOrderFeedback, ...]:
        return tuple(
            self._session.scalars(
                select(WorkOrderFeedback)
                .where(WorkOrderFeedback.work_order_id == work_order_id)
                .order_by(WorkOrderFeedback.id)
            ).all()
        )

    def get_rating_for_update(
        self,
        *,
        work_order_id: int,
        actor_user_id: int,
    ) -> WorkOrderFeedback | None:
        return self._session.scalar(
            select(WorkOrderFeedback)
            .where(
                WorkOrderFeedback.work_order_id == work_order_id,
                WorkOrderFeedback.actor_user_id == actor_user_id,
                WorkOrderFeedback.rating.is_not(None),
            )
            .order_by(WorkOrderFeedback.id.desc())
            .limit(1)
            .with_for_update()
        )

    def get_sla_clock(self, work_order_id: int) -> SlaClock | None:
        return self._session.scalar(
            select(SlaClock).where(SlaClock.work_order_id == work_order_id)
        )

    def get_sla_clock_for_update(self, work_order_id: int) -> SlaClock | None:
        return self._session.scalar(
            select(SlaClock)
            .where(SlaClock.work_order_id == work_order_id)
            .with_for_update()
        )

    def list_due_assignment_sla_work_orders_for_update(
        self,
        *,
        now: datetime,
        limit: int,
        work_order_id: int | None = None,
    ) -> tuple[WorkOrder, ...]:
        handled_response_timeout = (
            select(WorkOrderLog.id)
            .where(
                WorkOrderLog.work_order_id == WorkOrder.id,
                WorkOrderLog.request_id
                == func.concat(
                    "sla-assignment:",
                    WorkOrderAssignment.id,
                    ":response-timeout",
                ),
            )
            .exists()
        )
        active_assignment = and_(
            WorkOrderAssignment.id == WorkOrder.current_assignment_id,
            WorkOrderAssignment.work_order_id == WorkOrder.id,
            WorkOrderAssignment.status == "ACTIVE",
            WorkOrderAssignment.ended_at.is_(None),
            WorkOrderAssignment.timeout_at.is_(None),
        )
        filters = [
            or_(
                and_(
                    WorkOrder.status == "ASSIGNED",
                    WorkOrderAssignment.accept_at.is_(None),
                    SlaClock.accept_due_at <= now,
                ),
                and_(
                    WorkOrder.status == "ACCEPTED",
                    WorkOrderAssignment.accept_at.is_not(None),
                    ~handled_response_timeout,
                    SlaClock.respond_due_at <= now,
                ),
            )
        ]
        if work_order_id is not None:
            filters.append(WorkOrder.id == work_order_id)
        return tuple(
            self._session.scalars(
                select(WorkOrder)
                .join(SlaClock, SlaClock.work_order_id == WorkOrder.id)
                .join(WorkOrderAssignment, active_assignment)
                .where(*filters)
                .order_by(
                    case(
                        (
                            WorkOrder.status == "ASSIGNED",
                            SlaClock.accept_due_at,
                        ),
                        else_=SlaClock.respond_due_at,
                    ),
                    WorkOrder.id,
                )
                .limit(limit)
                .with_for_update(skip_locked=True)
            ).all()
        )

    def has_log_request_id(self, *, work_order_id: int, request_id: str) -> bool:
        return self._session.scalar(
            select(WorkOrderLog.id).where(
                WorkOrderLog.work_order_id == work_order_id,
                WorkOrderLog.request_id == request_id,
            )
        ) is not None

    def has_dispatch_wait_attempt(self, *, work_order_id: int, request_id: str) -> bool:
        return self._session.scalar(select(WorkOrderLog.id).where(
            WorkOrderLog.work_order_id == work_order_id,
            WorkOrderLog.event_code == "AUTO_DISPATCH_WAIT",
            WorkOrderLog.request_id == request_id,
        ).limit(1)) is not None

    def list_due_sla_clocks_for_update(
        self,
        *,
        now: datetime,
        limit: int,
        work_order_id: int | None = None,
    ) -> tuple[SlaClock, ...]:
        filters = [
            or_(
                (SlaClock.state == "OPEN") & (SlaClock.warning_at <= now),
                (SlaClock.state == "WARNING")
                & (SlaClock.complete_due_at <= now),
            )
        ]
        if work_order_id is not None:
            filters.append(SlaClock.work_order_id == work_order_id)
        return tuple(
            self._session.scalars(
                select(SlaClock)
                .where(*filters)
                .order_by(SlaClock.complete_due_at, SlaClock.id)
                .limit(limit)
                .with_for_update(skip_locked=True)
            ).all()
        )

    def list_sla_management_user_ids(
        self,
        *,
        tenant_id: int,
        property_id: int,
    ) -> tuple[int, ...]:
        rows = self._session.scalars(
            select(UserRole.user_id)
            .join(User, User.id == UserRole.user_id)
            .join(RolePermission, RolePermission.role_id == UserRole.role_id)
            .join(Permission, Permission.id == RolePermission.permission_id)
            .where(
                User.status == "ACTIVE",
                Permission.code == "work_order:decide",
                or_(
                    (UserRole.scope_type == "TENANT")
                    & (UserRole.tenant_id == tenant_id),
                    (UserRole.scope_type == "PROPERTY")
                    & (UserRole.tenant_id == tenant_id)
                    & (UserRole.property_id == property_id),
                ),
            )
            .distinct()
            .order_by(UserRole.user_id)
        ).all()
        return tuple(int(user_id) for user_id in rows)

    def get_active_assignee_user_id(self, work_order_id: int) -> int | None:
        return self._session.scalar(
            select(Employee.user_id)
            .join(
                WorkOrderAssignment,
                WorkOrderAssignment.employee_id == Employee.id,
            )
            .join(User, User.id == Employee.user_id)
            .where(
                WorkOrderAssignment.work_order_id == work_order_id,
                WorkOrderAssignment.status == "ACTIVE",
                WorkOrderAssignment.ended_at.is_(None),
                Employee.status == "ACTIVE",
                Employee.user_id.is_not(None),
                User.status == "ACTIVE",
            )
        )

    def list_queue_for_property(
        self,
        *,
        tenant_id: int,
        property_id: int,
        statuses: tuple[str, ...],
        after_id: int = 0,
        limit: int = 50,
    ) -> tuple[WorkOrder, ...]:
        return tuple(
            self._session.scalars(
                select(WorkOrder)
                .where(
                    WorkOrder.tenant_id == tenant_id,
                    WorkOrder.property_id == property_id,
                    WorkOrder.status.in_(statuses),
                    WorkOrder.id > after_id,
                )
                .order_by(WorkOrder.id)
                .limit(limit)
            ).all()
        )

    def list_for_assignee(
        self,
        *,
        employee_id: int,
        after_id: int = 0,
        limit: int = 50,
    ) -> tuple[WorkOrder, ...]:
        return tuple(
            self._session.scalars(
                select(WorkOrder)
                .join(
                    WorkOrderAssignment,
                    (WorkOrderAssignment.work_order_id == WorkOrder.id)
                    & (WorkOrderAssignment.status == "ACTIVE")
                    & WorkOrderAssignment.ended_at.is_(None),
                )
                .where(
                    WorkOrderAssignment.employee_id == employee_id,
                    WorkOrder.id > after_id,
                )
                .order_by(WorkOrder.id)
                .limit(limit)
            ).all()
        )

    def list_for_reporter(
        self,
        *,
        user_id: int,
        after_id: int = 0,
        limit: int = 50,
    ) -> tuple[WorkOrder, ...]:
        return tuple(
            self._session.scalars(
                select(WorkOrder)
                .where(
                    WorkOrder.reporter_user_id == user_id,
                    WorkOrder.id > after_id,
                )
                .order_by(WorkOrder.id)
                .limit(limit)
            ).all()
        )

    def add_file(self, record: FileObject) -> FileObject:
        self._session.add(record)
        self._session.flush()
        return record

    def add_work_order(self, record: WorkOrder) -> WorkOrder:
        self._session.add(record)
        self._session.flush()
        return record

    def add_content(self, record: WorkOrderContent) -> WorkOrderContent:
        self._session.add(record)
        self._session.flush()
        return record

    def add_media(self, record: WorkOrderMedia) -> WorkOrderMedia:
        self._session.add(record)
        self._session.flush()
        return record

    def add_assignment(self, record: WorkOrderAssignment) -> WorkOrderAssignment:
        self._session.add(record)
        self._session.flush()
        return record

    def add_log(self, record: WorkOrderLog) -> WorkOrderLog:
        self._session.add(record)
        self._session.flush()
        return record

    def add_process(self, record: WorkOrderProcess) -> WorkOrderProcess:
        self._session.add(record)
        self._session.flush()
        return record

    def add_receipt(self, record: WorkOrderReceipt) -> WorkOrderReceipt:
        self._session.add(record)
        self._session.flush()
        return record

    def add_feedback(self, record: WorkOrderFeedback) -> WorkOrderFeedback:
        self._session.add(record)
        self._session.flush()
        return record

    def add_sla_clock(self, record: SlaClock) -> SlaClock:
        self._session.add(record)
        self._session.flush()
        return record

    def add_idempotency(self, record: IdempotencyRecord) -> IdempotencyRecord:
        self._session.add(record)
        self._session.flush()
        return record

    def add_sla_policy(self, record: SlaPolicy) -> SlaPolicy:
        self._session.add(record)
        self._session.flush()
        return record

    def flush_sla_policy(self, record: SlaPolicy) -> SlaPolicy:
        self._session.flush((record,))
        return record
