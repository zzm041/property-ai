"""Internal responsible-round history without changing dispatch/quality policy."""
from collections.abc import Callable
from sqlalchemy.orm import Session
from app.core.errors import AppError,EmployeeErrorCode,IamErrorCode
from app.domain.work_order import EMPLOYEE_READ
from app.domain.identity import Principal,OrganizationScope,ScopeType
from app.domain.work_order import WORK_ORDER_DISPATCH
from app.repository.employee import EmployeeRepository
from app.repository.employee_history import EmployeeHistoryRepository
from app.schema.employee_history import EmployeeHistoryData,EmployeeHistoryItem,EmployeeHistoryFeedback
from app.service.identity.auth import AuthService
from app.service.identity.rbac import RbacService


def _forbidden():
    return AppError(code=IamErrorCode.FORBIDDEN,message='Permission denied',status_code=403)


class EmployeeHistoryService:
    def __init__(self,session:Session,*,repository=None,employees=None,
                 principal_loader:Callable[[int],Principal]|None=None):
        self._repository=repository or EmployeeHistoryRepository(session)
        self._employees=employees or EmployeeRepository(session)
        self._principal_loader=principal_loader or AuthService(session).load_principal

    def list_history(self,*,actor:Principal,employee_id:int,tenant_id:int,property_id:int,
                     before_id:int=0,limit:int=20)->EmployeeHistoryData:
        if (any(type(value) is not int or value<=0 for value in (employee_id,tenant_id,property_id))
                or type(before_id) is not int or before_id<0 or type(limit) is not int or not 1<=limit<=20):
            raise AppError(code=EmployeeErrorCode.INVALID_INPUT,message='Invalid history page',status_code=422)
        if not isinstance(actor,Principal) or actor.user_id<=0:raise _forbidden()
        try:
            current=self._principal_loader(actor.user_id)
        except AppError as exc:
            raise _forbidden() from exc
        if current.user_id!=actor.user_id or current.status!='ACTIVE':raise _forbidden()
        scope=OrganizationScope(ScopeType.PROPERTY,tenant_id,property_id)
        for permission in (EMPLOYEE_READ,WORK_ORDER_DISPATCH):
            RbacService.require_permission(current,permission,scope)
        if self._employees.get_property_in_tenant(tenant_id=tenant_id,property_id=property_id) is None:
            raise _forbidden()
        employee=self._employees.get_employee_in_tenant(tenant_id=tenant_id,employee_id=employee_id)
        if employee is None or not any(a.tenant_id==tenant_id and a.property_id==property_id
                for a in self._employees.list_assignments_for_employee(employee_id)):
            raise _forbidden()
        rows=self._repository.rounds(tenant_id=tenant_id,property_id=property_id,
                                     employee_id=employee_id,before_id=before_id,limit=limit+1)
        has_more=len(rows)>limit;rows=rows[:limit]
        feedback=self._repository.latest_feedback(tenant_id=tenant_id,property_id=property_id,
                                                  process_ids=[r['process_id'] for r in rows])
        declarations=self._repository.handling_declarations(tenant_id=tenant_id,property_id=property_id,
            work_order_ids=[r['work_order_id'] for r in rows],receipt_ids=[r['receipt_id'] for r in rows if r['receipt_id'] is not None])
        items=[]
        for row in rows:
            item=EmployeeHistoryItem(**row)
            latest=feedback.get(item.process_id)
            if latest:
                item.latest_feedback=EmployeeHistoryFeedback(id=latest['id'],recorded_at=latest['created_at'],
                    confirmed=latest['confirmed'],rating=latest['rating'],unresolved=bool(latest['unresolved_flag']))
            for declaration in declarations:
                value=declaration['handling']
                if declaration['work_order_id']!=item.work_order_id or not isinstance(value,dict):continue
                if (type(value.get('receipt_id')) is not int or value['receipt_id']!=item.receipt_id or
                        type(value.get('process_id')) is not int or value['process_id']!=item.process_id):continue
                mode=value.get('mode');participant=value.get('reported_participant_employee_id')
                if not isinstance(mode,str) or mode not in {'SELF','ASSISTED','EXTERNAL'}:continue
                if mode=='EXTERNAL' and participant is not None:continue
                if mode!='EXTERNAL' and (type(participant) is not int or participant<=0):continue
                item.handling_mode=mode;item.reported_participant_employee_id=participant
                item.declaration_log_id=declaration['id']
                break
            items.append(item)
        return EmployeeHistoryData(employee_id=employee_id,tenant_id=tenant_id,property_id=property_id,
                                   items=items,next_cursor=items[-1].process_id if has_more else None)
