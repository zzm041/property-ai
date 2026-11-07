"""Responsible-round history, not a ranking or a proxy for actual labor."""
from datetime import UTC,datetime
from types import SimpleNamespace as NS
from unittest.mock import MagicMock

import pytest

from app.core.errors import AppError
from app.domain.employee import EMPLOYEE_READ
from app.domain.iam import Principal,RoleGrant,OrganizationScope,ScopeType
from app.domain.work_order import WORK_ORDER_DISPATCH
from app.service.employee_history import EmployeeHistoryService

NOW=datetime(2026,11,2,tzinfo=UTC)


def actor(permissions=(EMPLOYEE_READ,WORK_ORDER_DISPATCH),property_id=9,status='ACTIVE'):
    return Principal(5,status,'synthetic',(RoleGrant('SUPERVISOR',OrganizationScope(ScopeType.PROPERTY,7,property_id),frozenset(permissions)),))


@pytest.fixture
def ctx():
    principal=actor();repository=MagicMock();employees=MagicMock();loader=MagicMock(return_value=principal)
    employees.get_property_in_tenant.return_value=NS(id=9,tenant_id=7)
    employees.get_employee_in_tenant.return_value=NS(id=11,tenant_id=7)
    employees.list_assignments_for_employee.return_value=[NS(tenant_id=7,property_id=9,status='ENDED')]
    row=dict(process_id=31,work_order_id=41,cycle_no=2,started_at=NOW,ended_at=None,
             category='electrical',current_order_status='WAIT_CONFIRM',receipt_id=51,
             receipt_finished_at=NOW,reported_resolved=1)
    repository.rounds.return_value=[row]
    repository.latest_feedback.return_value={31:dict(id=71,process_id=31,created_at=NOW,confirmed=None,rating=4,unresolved_flag=0)}
    repository.handling_declarations.return_value=[dict(id=61,work_order_id=41,handling=dict(receipt_id=51,process_id=31,mode='EXTERNAL',note='PRIVATE',reported_participant_employee_id=None))]
    session=MagicMock()
    service=EmployeeHistoryService(session,repository=repository,employees=employees,principal_loader=loader)
    return NS(service=service,repository=repository,employees=employees,loader=loader,row=row,session=session,
              query=lambda **kw:service.list_history(actor=principal,employee_id=11,tenant_id=7,property_id=9,**kw))


def test_responsible_round_is_not_inferred_independent_work_or_success(ctx):
    result=ctx.query();row=result.items[0]
    assert row.process_id==31 and row.handling_mode=='EXTERNAL'
    assert row.reported_participant_employee_id is None and row.declaration_log_id==61
    assert row.reported_resolved is True and row.latest_feedback.confirmed is None
    assert row.latest_feedback.rating==4 and row.latest_feedback.id==71
    assert 'PRIVATE' not in result.model_dump_json()
    assert not any(key in result.model_dump_json() for key in ('phone','comment','actor_user_id','completion_rate'))
    ctx.session.commit.assert_not_called()


@pytest.mark.parametrize('failure',['revoked','disabled','wrong_property','only_employee','only_dispatch','split_scope','missing_employee','no_assignment','identity_changed'])
def test_access_denied_before_history_read(ctx,failure):
    if failure=='revoked':ctx.loader.return_value=actor(())
    if failure=='disabled':ctx.loader.return_value=actor(status='DISABLED')
    if failure=='wrong_property':ctx.loader.return_value=actor(property_id=10)
    if failure=='only_employee':ctx.loader.return_value=actor((EMPLOYEE_READ,))
    if failure=='only_dispatch':ctx.loader.return_value=actor((WORK_ORDER_DISPATCH,))
    if failure=='split_scope':ctx.loader.return_value=Principal(5,'ACTIVE','synthetic',actor((EMPLOYEE_READ,)).grants+actor((WORK_ORDER_DISPATCH,),10).grants)
    if failure=='missing_employee':ctx.employees.get_employee_in_tenant.return_value=None
    if failure=='no_assignment':ctx.employees.list_assignments_for_employee.return_value=[NS(tenant_id=7,property_id=10)]
    if failure=='identity_changed':ctx.loader.return_value=Principal(6,'ACTIVE','synthetic',actor().grants)
    with pytest.raises(AppError):ctx.query()
    ctx.repository.rounds.assert_not_called()


@pytest.mark.parametrize('handling', [None,{}, {'receipt_id':99,'process_id':31,'mode':'SELF','reported_participant_employee_id':11},
    {'receipt_id':51,'process_id':32,'mode':'SELF','reported_participant_employee_id':11},
    {'receipt_id':51,'process_id':31,'mode':'UNVERIFIED','reported_participant_employee_id':11},
    {'receipt_id':51,'process_id':31,'mode':[],'reported_participant_employee_id':11},
    {'receipt_id':51,'process_id':31,'mode':'EXTERNAL','reported_participant_employee_id':11},
    {'receipt_id':51,'process_id':31,'mode':'SELF','reported_participant_employee_id':True}])
def test_malformed_or_foreign_declaration_is_unknown_not_owner_credit(ctx,handling):
    ctx.repository.handling_declarations.return_value=[dict(id=61,work_order_id=41,handling=handling)]
    row=ctx.query().items[0]
    assert row.handling_mode is None and row.declaration_log_id is None and row.reported_participant_employee_id is None


def test_delegate_self_report_does_not_become_owner_credit(ctx):
    ctx.repository.handling_declarations.return_value[0]['handling'].update(mode='SELF',reported_participant_employee_id=12)
    assert ctx.query().items[0].reported_participant_employee_id==12


def test_missing_feedback_stays_unknown(ctx):
    ctx.repository.latest_feedback.return_value={}
    ctx.row.update(receipt_id=None,receipt_finished_at=None,reported_resolved=None)
    row=ctx.query().items[0]
    assert row.reported_resolved is None and row.latest_feedback is None and row.handling_mode is None


def test_cursor_is_last_returned_round_not_extra_row(ctx):
    ctx.repository.rounds.return_value=[dict(ctx.row,process_id=32),dict(ctx.row,process_id=31)]
    result=ctx.query(limit=1,before_id=40)
    assert len(result.items)==1 and result.next_cursor==32
    assert ctx.repository.rounds.call_args.kwargs['limit']==2
    assert ctx.repository.rounds.call_args.kwargs['before_id']==40


@pytest.mark.parametrize('params',[{'limit':0},{'limit':21},{'before_id':-1}])
def test_invalid_page_is_rejected(ctx,params):
    with pytest.raises(AppError):ctx.query(**params)
    ctx.repository.rounds.assert_not_called()


def test_repository_queries_are_scoped_bounded_and_compile_for_mysql():
    from sqlalchemy.dialects import mysql
    from app.repository.employee_history import EmployeeHistoryRepository
    session=MagicMock();repository=EmployeeHistoryRepository(session)
    repository.rounds(tenant_id=7,property_id=9,employee_id=11,before_id=40,limit=21)
    statement=session.execute.call_args.args[0]
    sql=str(statement.compile(dialect=mysql.dialect(),compile_kwargs={'literal_binds':True}))
    assert 'work_order_processes.assignee_id = 11' in sql and 'work_order_processes.property_id = 9' in sql
    assert 'work_order_processes.tenant_id = 7' in sql and 'work_order_processes.id < 40' in sql
    assert 'LIMIT 21' in sql and 'contact_phone' not in sql
    repository.latest_feedback(tenant_id=7,property_id=9,process_ids=[31])
    sql=str(session.execute.call_args.args[0].compile(dialect=mysql.dialect(),compile_kwargs={'literal_binds':True}))
    assert 'max(work_order_feedbacks.id)' in sql and 'process_id IN (31)' in sql
    assert 'actor_user_id' not in sql and 'comment' not in sql
    repository.handling_declarations(tenant_id=7,property_id=9,work_order_ids=[41],receipt_ids=[51])
    sql=str(session.execute.call_args.args[0].compile(dialect=mysql.dialect(),compile_kwargs={'literal_binds':True}))
    assert 'JSON_EXTRACT' in sql and 'LIMIT 80' in sql and 'work_order_logs.property_id = 9' in sql
    session.commit.assert_not_called()


def test_route_rejects_header_mismatch_before_constructing_service(monkeypatch):
    from starlette.requests import Request
    import app.api.v1.admin_employee as route
    service=MagicMock();monkeypatch.setattr(route,'EmployeeHistoryService',service)
    with pytest.raises(AppError):
        route.employee_work_history(employee_id=11,request=Request({'type':'http','headers':[]}),
            actor=actor(),session=MagicMock(),tenant_id=7,property_id=9,before_id=0,limit=20,x_property_id='10')
    service.assert_not_called()
