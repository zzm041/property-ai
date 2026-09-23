"""020 pure projection/query/page simulations; no database or runtime fixture."""
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
import subprocess

import pytest

from app.api.v1.work_orders import work_order_display_data
from app.core.errors import AppError, WorkOrderErrorCode
from app.model.work_order import WorkOrder
from app.repository.work_order import WorkOrderRepository
from app.schema.work_order import WorkOrderData, WorkOrderDetailData, WorkOrderMediaData
from app.service.work_order import WorkOrderService

ROOT = Path(__file__).resolve().parents[3]
NOW = datetime(2026, 9, 23, 18, 30, tzinfo=UTC)


def detail_at(instant=NOW):
    return WorkOrderDetailData(work_order=WorkOrderData(
        id=31, tenant_id=2, property_id=7, house_id=8, reporter_user_id=4,
        status='CLOSED', process_reason='NONE', category='ELECTRICAL', severity='LOW',
        priority='LOW', version=5, current_assignment_id=None, incident_id=None,
        sla_due_at=None, created_at=instant, updated_at=instant,
        **{k: None for k in ('assigned_at', 'accepted_at', 'started_at', 'resolved_at', 'closed_at')},
    ))


@pytest.mark.parametrize('timezone,instant,expected', [
    ('Asia/Shanghai', NOW, '2026-09-24 02:30:00 UTC+08:00'),
    ('America/New_York', datetime(2026, 3, 8, 6, 59, tzinfo=UTC), '2026-03-08 01:59:00 UTC-05:00'),
    ('America/New_York', datetime(2026, 3, 8, 7, 1, tzinfo=UTC), '2026-03-08 03:01:00 UTC-04:00'),
    ('not/a-timezone', NOW, '2026-09-23 18:30:00 UTC+00:00'),
    (None, NOW, '2026-09-23 18:30:00 UTC+00:00'),
])
def test_property_time_labels_preserve_raw_utc_and_dst(timezone, instant, expected):
    detail = detail_at(instant)
    before = detail.model_dump(mode='json')
    projection = work_order_display_data(detail, {'property_timezone': timezone, 'assignee_names': {9: '合成维修人员'}})
    raw = before['work_order']['created_at']
    assert projection.timestamp_labels[raw] == expected
    assert detail.model_dump(mode='json') == before
    assert projection.assignee_names == {9: '合成维修人员'}
    assert projection.property_timezone == (timezone if timezone and timezone != 'not/a-timezone' else 'UTC')


def test_timestamp_projection_includes_nested_media_without_inventing_dates():
    detail = detail_at()
    nested = datetime(2026, 9, 23, 1, 2, 3, 123456, tzinfo=UTC)
    detail.media = [WorkOrderMediaData(id=12, file_id=20, kind='IMAGE', taken_phase='REPORT', uploader_user_id=4, created_at=nested)]
    labels = work_order_display_data(detail, {'property_timezone': 'Asia/Shanghai'}).timestamp_labels
    assert labels['2026-09-23T01:02:03.123456Z'] == '2026-09-23 09:02:03 UTC+08:00'
    assert len(labels) == 2
    assert 'None' not in labels


def test_names_are_selected_only_from_order_scoped_assignments_and_processes():
    session = MagicMock()
    session.scalar.return_value = 'Asia/Shanghai'
    session.execute.return_value.all.return_value = [(9, '合成员工'), (10, ''), (11, ' ')]
    result = WorkOrderRepository(session).get_display_context(WorkOrder(id=31, tenant_id=2, property_id=7))
    assert result == {'property_timezone': 'Asia/Shanghai', 'assignee_names': {9: '合成员工'}}
    property_query = session.scalar.call_args.args[0]
    assert set(property_query.compile().params.values()) == {2, 7}
    query = session.execute.call_args.args[0]
    sql = str(query)
    for fragment in ('work_order_assignments.work_order_id', 'work_order_processes.work_order_id',
                     'work_order_assignments.tenant_id', 'work_order_processes.tenant_id',
                     'work_order_assignments.property_id', 'work_order_processes.property_id',
                     'employees.tenant_id', 'users.display_name', 'UNION'):
        assert fragment in sql
    assert set(query.compile().params.values()) == {31, 2, 7}
    assert 'phone' not in sql and 'password' not in sql


def test_missing_scoped_property_does_not_fetch_any_employee_name():
    session = MagicMock()
    session.scalar.return_value = None
    assert WorkOrderRepository(session).get_display_context(WorkOrder(id=31, tenant_id=2, property_id=7)) == {
        'property_timezone': 'UTC', 'assignee_names': {},
    }
    session.execute.assert_not_called()


def test_display_metadata_is_never_loaded_before_order_read_authorization():
    service = WorkOrderService.__new__(WorkOrderService)
    service._repository = MagicMock()
    service.get_work_order = MagicMock(side_effect=AppError(code=WorkOrderErrorCode.NOT_FOUND, message='Not found', status_code=404))
    with pytest.raises(AppError):
        service.get_work_order_detail(actor=MagicMock(), work_order_id=31)
    assert not service._repository.mock_calls
    record = SimpleNamespace(id=31)
    service.get_work_order.side_effect = None
    service.get_work_order.return_value = record
    service._repository.get_display_context.return_value = {'property_timezone': 'UTC', 'assignee_names': {}}
    result = service.get_work_order_detail(actor=MagicMock(), work_order_id=31)
    assert result[0] is record
    assert result[-1] == {'property_timezone': 'UTC', 'assignee_names': {}}
    service._repository.get_display_context.assert_called_once_with(record)


SCRIPT = r'''
const fs=require('fs'),path=require('path'),vm=require('vm'),assert=require('assert/strict');
const [root,scenario]=process.argv.slice(1),p=require(path.join(root,'miniprogram/utils/work-order-display'));
const statuses={PENDING_ANALYSIS:'待分析',PENDING_ASSIGNMENT:'待安排人员',ASSIGNED:'已派单',ACCEPTED:'已接单',PROCESSING:'处理中',WAIT_CONFIRM:'待住户确认',COMPLETED:'已完成',CANCELLED:'已撤销',CLOSED:'已归档'};
if(scenario in statuses){assert.equal(p.statusLabel(scenario),statuses[scenario]);process.exit(0);}
const raw={work_order:{id:31,status:'PROCESSING',process_reason:'EXTERNAL_COORDINATION',version:7,created_at:'2026-09-23T18:30:00Z'},
 display:{property_timezone:'Asia/Shanghai',assignee_names:{9:'合成员工'},timestamp_labels:{'2026-09-23T18:30:00Z':'2026-09-24 02:30:00 UTC+08:00'}},
 assignments:[{id:8,employee_id:9}],processes:[{id:11,cycle_no:1,assignee_id:9,started_at:'2026-09-23T18:30:00Z'},{id:12,cycle_no:2,assignee_id:10}],
 receipts:[{id:21,process_id:11,problem_type:'ELECTRICAL'},{id:22,process_id:12,problem_type:'__proto__'}],
 feedbacks:[{id:40,process_id:11,unresolved_flag:true,created_at:'2026-09-23T18:30:00Z'}],
 media:[{id:1,file_id:2,kind:'IMAGE',taken_phase:'REPORT',created_at:'2026-09-23T18:30:00Z'}],
 logs:[{id:1,from_status:null,to_status:'PENDING_ANALYSIS',event_code:'CREATE',actor_type:'RESIDENT',created_at:'2026-09-23T18:30:00Z'}]};
const saved=JSON.stringify(raw),view=p.presentDetail(raw);
assert.equal(JSON.stringify(raw),saved,'projection must not mutate business data');
if(scenario==='projection'){
 assert.equal(view.reasonLabel,'物业继续处理中');assert.equal(view.timezoneLabel,'Asia/Shanghai');assert.equal(view.createdAtLabel,'2026-09-24 02:30:00 UTC+08:00');
 assert.equal(view.processes[0].assigneeLabel,'合成员工（员工 #9）');assert.match(view.processes[1].assigneeLabel,/未提供展示名/);
 assert.equal(view.receipts[1].problemTypeLabel,'__proto__');assert.equal(view.logs[0].eventLabel,'提交报修');assert.equal(view.logs[0].actorLabel,'住户');assert.equal(view.media[0].phaseLabel,'报修附件');process.exit(0);
}
if(scenario==='unknown'){
 assert.match(p.statusLabel('__proto__'),/未知状态/);assert.equal(p.utcLabel(null),'未记录');assert.equal(p.utcLabel('invalid'),'时间不可用');
 assert.equal(p.utcLabel('2026-09-23T18:30:00Z'),'2026-09-23 18:30:00 UTC');
 const x=p.presentDetail({work_order:{status:'FUTURE'},logs:[{to_status:'FUTURE',event_code:'FUTURE',actor_type:'FUTURE'}]});assert.match(x.logs[0].eventLabel,/FUTURE/);assert.equal(x.timezoneLabel,'UTC');process.exit(0);
}
let page,calls=0;
const api={workOrder:()=>{calls++;return Promise.resolve(raw);}};
vm.runInNewContext(fs.readFileSync(path.join(root,'miniprogram/pages/work-orders/detail.js'),'utf8'),{
 Page:x=>page=x,wx:{},setTimeout,clearTimeout,
 require:n=>n==='../../services/api'?api:n==='../../services/auth'?{setLastWorkOrderId(){}}:require(path.join(root,'miniprogram/pages/work-orders',n))
});page.setData=x=>Object.assign(page.data,x);page.id=31;
(async()=>{await page.reload();assert.equal(calls,1);assert.equal(page.data.workOrder.version,7);assert.equal(page.data.workOrder.status,'PROCESSING');assert.equal(page.data.statusLabel,'处理中');
 assert.equal(page.data.processHistory[0].receipt.id,21);assert.equal(page.data.processHistory[1].receipt.id,22);assert.equal(page.data.processHistory[0].feedbacks[0].id,40);assert.equal(page.data.processHistory[1].feedbacks.length,0);
 assert(page.data.canAppend&&!page.data.canCancel&&!page.data.canReopen);assert.equal(JSON.stringify(raw),saved);
 raw.work_order.status='WAIT_CONFIRM';await page.reload();assert(page.data.canConfirm&&page.data.canReopen);assert.equal(page.data.statusLabel,'待住户确认');
 raw.work_order.status='CLOSED';await page.reload();assert(!page.data.canConfirm&&!page.data.canAppend&&page.data.canRate);assert.equal(page.data.statusLabel,'已归档');
})().catch(e=>{console.error(e);process.exitCode=1;});
'''


@pytest.mark.parametrize('scenario', [
    'PENDING_ANALYSIS', 'PENDING_ASSIGNMENT', 'ASSIGNED', 'ACCEPTED', 'PROCESSING',
    'WAIT_CONFIRM', 'COMPLETED', 'CANCELLED', 'CLOSED', 'projection', 'unknown', 'page',
])
def test_readable_page_projection_preserves_business_state(scenario):
    result = subprocess.run(['node', '-e', SCRIPT, str(ROOT), scenario], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
