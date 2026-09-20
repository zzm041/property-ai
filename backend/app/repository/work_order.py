"""Query/add/flush for WorkOrder aggregates. Service owns commits."""

from datetime import datetime

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.model.property import House, Property
from app.model.employee import Employee
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
        )

    def get_house(self, house_id: int) -> House | None:
        return self._session.get(House, house_id)

    def get_property(self, property_id: int) -> Property | None:
        return self._session.get(Property, property_id)

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
        filters = [
            or_(
                (WorkOrder.status == "ASSIGNED") & (SlaClock.accept_due_at <= now),
                (WorkOrder.status == "ACCEPTED") & (SlaClock.respond_due_at <= now),
            )
        ]
        if work_order_id is not None:
            filters.append(WorkOrder.id == work_order_id)
        return tuple(
            self._session.scalars(
                select(WorkOrder)
                .join(SlaClock, SlaClock.work_order_id == WorkOrder.id)
                .where(*filters)
                .order_by(WorkOrder.id)
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
