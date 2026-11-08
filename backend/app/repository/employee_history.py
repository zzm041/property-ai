"""Bounded scoped reads, with separate per-round sources and no writes."""
from sqlalchemy import and_,func,select
from sqlalchemy.orm import Session
from app.model.work_order import WorkOrder,WorkOrderProcess,WorkOrderReceipt,WorkOrderFeedback,WorkOrderLog


class EmployeeHistoryRepository:
    def __init__(self,session:Session):
        self._session=session

    def rounds(self,*,tenant_id:int,property_id:int,employee_id:int,before_id:int,limit:int):
        p,w,r=WorkOrderProcess,WorkOrder,WorkOrderReceipt
        query=select(p.id.label('process_id'),p.work_order_id,p.cycle_no,p.started_at,p.ended_at,
                     w.category,w.status.label('current_order_status'),r.id.label('receipt_id'),
                     r.finished_at.label('receipt_finished_at'),r.resolved.label('reported_resolved')).select_from(p)
        query=query.join(w,and_(w.id==p.work_order_id,w.tenant_id==p.tenant_id,w.property_id==p.property_id))
        query=query.outerjoin(r,and_(r.process_id==p.id,r.work_order_id==p.work_order_id,
                                    r.tenant_id==p.tenant_id,r.property_id==p.property_id))
        query=query.where(p.tenant_id==tenant_id,p.property_id==property_id,p.assignee_id==employee_id)
        if before_id:
            query=query.where(p.id<before_id)
        return list(self._session.execute(query.order_by(p.id.desc()).limit(limit)).mappings())

    def latest_feedback(self,*,tenant_id:int,property_id:int,process_ids:list[int]):
        if not process_ids:return {}
        f=WorkOrderFeedback
        latest=select(func.max(f.id).label('id')).where(f.tenant_id==tenant_id,
            f.property_id==property_id,f.process_id.in_(process_ids)).group_by(f.process_id)
        query=select(f.id,f.process_id,f.created_at,f.confirmed,f.rating,f.unresolved_flag).where(
            f.tenant_id==tenant_id,f.property_id==property_id,f.id.in_(latest))
        return {row['process_id']:row for row in self._session.execute(query).mappings()}

    def handling_declarations(self,*,tenant_id:int,property_id:int,work_order_ids:list[int],receipt_ids:list[int]):
        if not receipt_ids:return []
        log=WorkOrderLog
        query=select(log.id,log.work_order_id,log.payload_json['receipt_handling_v1'].label('handling')).where(
            log.tenant_id==tenant_id,log.property_id==property_id,log.work_order_id.in_(work_order_ids),
            log.event_code.in_(('RECEIPT_RESOLVED','RECEIPT_UNRESOLVED')),
            log.payload_json['receipt_handling_v1']['receipt_id'].as_integer().in_(receipt_ids),
        ).order_by(log.id.desc()).limit(80)
        return list(self._session.execute(query).mappings())
