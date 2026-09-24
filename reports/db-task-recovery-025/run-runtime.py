"""Checkpointed new-run APIs; fault control lives only in dedicated Worker."""
import json
from pathlib import Path
import sys
import time
sys.path.insert(0,str(Path(__file__).parent))
from common import *
from sqlalchemy import text

ctx=read_context();c=Client(ctx['run_id']);s=settings()
out=Path(__file__).with_name('runtime-evidence.json')
e=json.loads(out.read_text(encoding='utf-8')) if out.exists() else {'task_id':'DB-TASK-RECOVERY-025','run_id':ctx['run_id'],'mode':'real API/DB/independent Worker; Development models; not UI','steps':{},'requests':[]}
def save():
    save_context(ctx);e['requests'].extend(c.requests);c.requests.clear();public_write(out.name,e)
def req(method,path,actor='resident',**kw):return c.data(method,path,token=ctx['tokens'][actor],label=kw.pop('label','observe'),**kw)
def detail(label):return req('GET',f"/work-orders/{ctx['orders'][label]}",label='detail-'+label)
def until(label,condition):
    deadline=time.monotonic()+90
    while time.monotonic()<deadline:
        d=detail(label)
        if condition(d):return d
        time.sleep(.25)
    raise RuntimeError('025 bounded wait exceeded: '+label)
def create(label):
    if label not in ctx['orders']:
        assert len(ctx['orders'])<6
        body={'house_id':ctx['houses'][label],'contact_phone':ctx['phones']['resident'],
              'text':ctx['run_id']+' '+label+'：水龙头滴水，插座不通电，无烟无火花无焦味，普通维修组统一处理。'}
        d=req('POST','/work-orders',body=body,headers={'Idempotency-Key':ctx['run_id']+'-'+label},label='case-'+label)
        ctx['orders'][label]=d['id'];save()
    return ctx['orders'][label]
def rows(label):
    engine=create_database_engine(s.active_database_url)
    with engine.connect() as conn:
        conn.execute(text('SET TRANSACTION READ ONLY'))
        oid=ctx['orders'][label];r={'order_id':oid}
        r['analysis']=[dict(x) for x in conn.execute(text('SELECT id,analysis_version,status,input_hash,error_code FROM work_order_analyses WHERE work_order_id=:id ORDER BY id'),{'id':oid}).mappings()]
        r['calls']=[dict(x) for x in conn.execute(text('SELECT id,provider_type,attempt,status,model_version FROM ai_call_logs WHERE work_order_id=:id ORDER BY id'),{'id':oid}).mappings()]
        r['assignments']=[dict(x) for x in conn.execute(text('SELECT id,employee_id,status,assigned_source,assigned_by_user_id FROM work_order_assignments WHERE work_order_id=:id ORDER BY id'),{'id':oid}).mappings()]
        r['logs']=[dict(x) for x in conn.execute(text('SELECT id,event_code,payload_json FROM work_order_logs WHERE work_order_id=:id ORDER BY id'),{'id':oid}).mappings()]
        for entry in r['logs']:
            if isinstance(entry['payload_json'],str):entry['payload_json']=json.loads(entry['payload_json'])
        r['employee_load']=[dict(x) for x in conn.execute(text('SELECT business_date,current_load FROM employee_daily_statuses WHERE employee_id=:id ORDER BY business_date'),{'id':ctx['employee_id']}).mappings()]
        phone=conn.execute(text('SELECT contact_phone_e164 FROM work_orders WHERE id=:id'),{'id':oid}).scalar_one()
        r['contact_snapshot_correct']=phone=='+86'+ctx['phones']['resident'].removeprefix('+86')
    engine.dispose();return r
def finish(label):
    oid=ctx['orders'][label];d=detail(label)
    for state,op in [('ASSIGNED','accept'),('ACCEPTED','start')]:
        if d['work_order']['status']==state:
            req('POST',f'/staff/work-orders/{oid}/{op}','staff',body={'version':d['work_order']['version']},label=label+'-'+op);d=detail(label)
    if d['work_order']['status']=='PROCESSING':
        req('POST',f'/staff/work-orders/{oid}/receipts','staff',body={'version':d['work_order']['version'],'problem_type':'普通维修','method':'合成验证：修复水龙头滴水并检查插座供电，逐项验证两个问题解决。','resolved':True},label=label+'-receipt')
    d=detail(label)
    if d['work_order']['status']=='WAIT_CONFIRM':req('POST',f'/work-orders/{oid}/confirm',body={'version':d['work_order']['version']},label=label+'-confirm')
    assert detail(label)['work_order']['status']=='CLOSED'
def step(name,fn):
    if name not in e['steps']:
        e['steps'][name]=fn();save();print(json.dumps({'step':name,'status':'verified'}),flush=True)
def normal_case(label):
    create(label);until(label,lambda d:d['work_order']['status']=='ASSIGNED')
    result=rows(label)
    assert len(result['calls'])==1 and len(result['assignments'])==1
    assert result['assignments'][0]['assigned_source']=='AUTO'
    finish(label);return result
def combo_create():
    create('dispatch');until('dispatch',lambda d:d['work_order']['status']=='ASSIGNED')
    create('busy');until('busy',lambda d:any(x['event_code']=='AUTO_DISPATCH_WAIT' for x in d['logs']))
    assert detail('busy')['work_order']['status']=='PENDING_ASSIGNMENT'
    a=rows('dispatch');b=rows('busy');assert len(a['calls'])==1 and len(a['assignments'])==1 and not b['assignments']
    return {'recovered':a,'busy':b}
def duplicate():
    from celery import Celery
    before=rows('dispatch');payload=next(x['payload_json'] for x in before['logs'] if x['event_code']=='AI_TASK_ATTEMPT')
    Celery('025-explicit-duplicate',broker=s.celery_broker_url).send_task('app.worker.tasks.analyze_work_order_task',args=(ctx['orders']['dispatch'],ctx['run_id']+'-duplicate-check'),task_id=payload['task_id'],headers={'analysis_expected_version':1},queue=s.resolved_celery_queue)
    # Explicit duplicate-only test; original already AUTO before this send.
    time.sleep(1)
    after=rows('dispatch');assert before==after
    return {'mode':'duplicate-delivery test, not recovery replay','task_id':payload['task_id'],'before':before,'after':after}
def combo_release():
    finish('dispatch');until('busy',lambda d:d['work_order']['status']=='ASSIGNED');b=rows('busy')
    assert len(b['calls'])==1 and len(b['assignments'])==1
    return {'released_order':ctx['orders']['dispatch'],'automatically_resumed':b}
def cancellation():
    create('cancel');log=ROOT/'tmp/db-task-recovery-025/worker-02.stdout.log'
    end=time.monotonic()+10
    while time.monotonic()<end:
        if 'before-order-lock' in log.read_text(encoding='utf-8') and 'case-cancel' in log.read_text(encoding='utf-8'):break
        time.sleep(.1)
    else:raise RuntimeError('Cancellation fixture did not start')
    d=detail('cancel');req('POST',f"/work-orders/{ctx['orders']['cancel']}/cancel",body={'version':d['work_order']['version'],'reason':'025合成验证：恢复期间正常撤销'},label='cancel-during-recovery')
    time.sleep(6);result=rows('cancel')
    assert detail('cancel')['work_order']['status']=='CANCELLED' and not result['calls'] and not result['assignments']
    return result
def persistent():
    create('persistent');until('persistent',lambda d:any(x['event_code']=='AI_TASK_FAILED' for x in d['logs']))
    result=rows('persistent');failure=next(x['payload_json'] for x in result['logs'] if x['event_code']=='AI_TASK_FAILED')
    assert failure['attempt']==3 and failure['error_code']=='AI.DATABASE_RETRY_EXHAUSTED'
    assert not result['assignments'];return result
try:
    phase=sys.argv[1]
    if phase=='prepare':step('prepare-recovery',lambda:normal_case('prepare'))
    elif phase=='combo':step('combo-create',combo_create);step('duplicate-delivery',duplicate)
    elif phase=='release':step('combo-release',combo_release)
    elif phase=='remaining':
        step('close-busy',lambda:(finish('busy') or {'closed':ctx['orders']['busy']}))
        step('cancel-during-recovery',cancellation);step('bounded-exhaustion',persistent)
        step('commit-ack-uncertain',lambda:normal_case('commit'))
    else:raise ValueError('Unknown phase')
finally:save();c.close()
