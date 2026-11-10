"""Internal, self-reported participation is not assignment or quality credit."""
from datetime import UTC, datetime
from types import SimpleNamespace as NS
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from app.core.errors import AppError
from app.domain.work_order import WorkOrderActorType
from app.schema.work_order import StaffReceiptRequest
from app.service.rbac import RbacService
from app.service.work_order import WorkOrderService

BASE=dict(problem_type='PLUMBING',method='synthetic repair',resolved=True)

@pytest.mark.parametrize('mode,note', [('SELF',None),('ASSISTED',' support team '),('EXTERNAL',' contractor '),(None,None)])
def test_schema_accepts_optional_participation(mode,note):
    value=StaffReceiptRequest(**BASE,handling_mode=mode,handling_note=note)
    assert value.handling_mode==mode

@pytest.mark.parametrize('mode,note',[('UNKNOWN',None),('ASSISTED','  '),('EXTERNAL',None),(None,'unclassified'),('SELF','x'*256)])
def test_schema_rejects_ambiguous_or_oversized_participation(mode,note):
    with pytest.raises(ValidationError):StaffReceiptRequest(**BASE,handling_mode=mode,handling_note=note)

def public_service():
    service=WorkOrderService.__new__(WorkOrderService)
    service._execute=MagicMock(side_effect=lambda f:f())
    service._submit_receipt=MagicMock()
    return service

def test_public_service_normalizes_note_and_legacy_default():
    service=public_service()
    service.submit_receipt(actor=NS(user_id=7),work_order_id=41,request_id='synthetic',**BASE,
                           handling_mode='EXTERNAL',handling_note='  support team  ')
    assert service._submit_receipt.call_args.kwargs['handling_note']=='support team'
    service.submit_receipt(actor=NS(user_id=7),work_order_id=41,request_id='synthetic',**BASE)
    assert service._submit_receipt.call_args.kwargs['handling_mode'] is None

@pytest.mark.parametrize('mode,note',[('UNKNOWN',None),('ASSISTED',' '),(None,'unclassified'),('SELF','x'*256)])
def test_direct_service_rejects_invalid_input_before_transaction(mode,note):
    service=public_service()
    with pytest.raises(AppError) as exc:
        service.submit_receipt(actor=NS(user_id=7),work_order_id=41,request_id='synthetic',**BASE,
                               handling_mode=mode,handling_note=note)
    assert exc.value.status_code==422
    service._execute.assert_not_called()

@pytest.fixture
def receipt_case(monkeypatch):
    service=WorkOrderService.__new__(WorkOrderService)
    actor=NS(user_id=7)
    record=NS(id=41,tenant_id=2,property_id=3,reporter_user_id=8,status='PROCESSING',process_reason='NONE',version=4,resolved_at=None)
    process=NS(id=9,assignee_id=31,ended_at=None)
    service._session=MagicMock()
    service._clock=lambda:datetime(2026,11,2,tzinfo=UTC)
    service._lock_assignee_action=MagicMock(return_value=(actor,record,NS(employee_id=31),NS(id=31,user_id=99)))
    service._require_actor_employee=MagicMock(return_value=NS(id=88,user_id=7))
    service._property_scope=MagicMock()
    service._repository=MagicMock()
    service._repository.get_current_process.return_value=process
    service._repository.get_receipt_for_process.return_value=None
    def save(receipt):
        receipt.id=77
        return receipt
    service._repository.add_receipt.side_effect=save
    service._actor_type=lambda *_:WorkOrderActorType.STAFF
    for method in ('_attach_media','_append_audit','_notify','_notify_users','_stop_sla'):
        setattr(service,method,MagicMock())
    monkeypatch.setattr(RbacService,'require_permission',lambda *_:None)
    return service,actor,record,process

@pytest.mark.parametrize('resolved',[True,False])
@pytest.mark.parametrize('mode,note,employee_id',[('SELF',None,88),('ASSISTED','synthetic support',88),('EXTERNAL','synthetic contractor',None),(None,None,None)])
def test_receipt_logs_link_actual_declaration_without_changing_assignee(receipt_case,resolved,mode,note,employee_id):
    service,actor,record,process=receipt_case
    service._submit_receipt(actor=actor,work_order_id=41,request_id='synthetic',problem_type='PLUMBING',method='synthetic',resolved=resolved,
        materials=None,fee_amount=None,fee_flag='NONE',unresolved_reason='follow up' if not resolved else None,follow_up=None,
        before_file_ids=(),after_file_ids=(),version=4,handling_mode=mode,handling_note=note)
    log=service._repository.add_log.call_args.args[0]
    if mode:
        declaration=log.payload_json['receipt_handling_v1']
        assert declaration==dict(receipt_id=77,process_id=9,mode=mode,note=note,reported_participant_employee_id=employee_id)
        assert log.actor_user_id==7 and mode not in log.payload_json['reason']
        assert '自报' in log.payload_json['reason']
    else:
        assert 'receipt_handling_v1' not in log.payload_json
    assert process.assignee_id==31
    assert record.status==('WAIT_CONFIRM' if resolved else 'PROCESSING')

def test_duplicate_receipt_cannot_rewrite_participation(receipt_case):
    service,actor,record,_=receipt_case
    service._repository.get_receipt_for_process.return_value=object()
    with pytest.raises(AppError) as exc:
        service._submit_receipt(actor=actor,work_order_id=41,request_id='synthetic',problem_type='PLUMBING',method='synthetic',resolved=True,
            materials=None,fee_amount=None,fee_flag='NONE',unresolved_reason=None,follow_up=None,before_file_ids=(),after_file_ids=(),version=4,
            handling_mode='EXTERNAL',handling_note='later replacement')
    assert exc.value.status_code==409
    service._repository.add_receipt.assert_not_called()
    service._repository.add_log.assert_not_called()


def test_route_forwards_declared_participation():
    from starlette.requests import Request
    from app.api.v1.staff_work_orders import submit_receipt
    from test_work_order_visibility import order
    service=MagicMock()
    service.submit_receipt.return_value=order()
    submit_receipt(41,StaffReceiptRequest(**BASE,handling_mode='EXTERNAL',handling_note='support team'),
                   Request({'type':'http','headers':[]}),NS(user_id=7),service)
    assert service.submit_receipt.call_args.kwargs['handling_mode']=='EXTERNAL'
    assert service.submit_receipt.call_args.kwargs['handling_note']=='support team'


def test_internal_handling_is_readable_by_property_and_hidden_from_resident():
    from starlette.requests import Request
    from app.api.v1.work_orders import get_work_order
    from test_work_order_visibility import payload_context,log
    raw=list(payload_context())
    event=log('RECEIPT_RESOLVED',reason='实际处理（提交者自报）：由支援人员处理；private-support')
    event.payload_json['receipt_handling_v1']={'mode':'EXTERNAL','note':'private-support','receipt_id':5,'process_id':1}
    raw[7]=(event,)
    service=MagicMock()
    service.get_work_order_detail.return_value=tuple(raw)
    service.is_overdue.return_value=False
    request=Request({'type':'http','headers':[]})
    resident=get_work_order(41,request,NS(user_id=7),service)['data']
    internal=get_work_order(41,request,NS(user_id=8),service,audience='property')['data']
    assert 'private-support' not in str(resident)
    assert internal['logs'][0]['reason']==event.payload_json['reason']
    assert resident['receipts'][0]['unresolved_reason']=='still leaking'
