"""Bounded real DB contention on this run's sole available employee.

No SQL data edits: hold/release one Employee SELECT FOR UPDATE for at most 15s
while two real Worker tasks started by normal create APIs contend. Read-lock
fixture is explicitly NOT a page action, model test or manual assignment.
"""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import runpy
import sys
import time

ROOT=Path(__file__).resolve().parents[2]
h=runpy.run_path(str(ROOT/'reports/notification-events-inbox-013/run-runtime.py'))
from auth001_isolated_runtime import build_isolated_settings
from app.core.database import assert_isolated_auth_database, create_database_engine
from sqlalchemy import text
s=build_isolated_settings();assert_isolated_auth_database(s,database_url=s.active_database_url)
private=ROOT/'tmp/maintenance-contact-dispatch-024/context.json'
ctx=json.loads(private.read_text(encoding='utf-8'))
drained='--drained' in sys.argv
last='--four-threads' in sys.argv
short='--short-barrier' in sys.argv or last
drained=drained or short
labels=('contended7','contended8') if last else (('contended5','contended6') if short else (('contended3','contended4') if drained else ('contended1','contended2')))
out=Path(__file__).with_name('contended-dispatch-four.json' if last else ('contended-dispatch-short.json' if short else ('contended-dispatch-drained.json' if drained else 'contended-dispatch.json')))
e=json.loads(out.read_text(encoding='utf-8')) if out.exists() else {'run_id':ctx['run_id'],'requests':[],'steps':{}}
c=h['Client'](ctx['run_id']+'-contended')
def save():
    e['requests'].extend(c.requests);c.requests.clear()
    out.write_text(json.dumps(e,ensure_ascii=False,indent=2),encoding='utf-8')
    private.write_text(json.dumps(ctx,ensure_ascii=False,indent=2),encoding='utf-8')
def req(method,path,actor='resident',body=None,label='fixture'):
    return c.data(method,path,token=ctx['tokens'][actor],body=body,label=label)
def detail(oid):return req('GET',f'/work-orders/{oid}')
def finish(oid,actor):
    d=detail(oid)
    for state,endpoint in [('ASSIGNED','accept'),('ACCEPTED','start')]:
        if d['work_order']['status']==state:
            req('POST',f'/staff/work-orders/{oid}/{endpoint}',actor,{'version':d['work_order']['version']});d=detail(oid)
    if d['work_order']['status']=='PROCESSING':req('POST',f'/staff/work-orders/{oid}/receipts',actor,{'version':d['work_order']['version'],'problem_type':'普通维修','method':'合成场景逐项完成维修与检查。','resolved':True})
    d=detail(oid)
    if d['work_order']['status']=='WAIT_CONFIRM':req('POST',f'/work-orders/{oid}/confirm',body={'version':d['work_order']['version']})
    assert detail(oid)['work_order']['status']=='CLOSED'
def create(label,house):
    client=h['Client'](ctx['run_id']+'-'+label)
    try:
        d=client.data('POST','/work-orders',token=ctx['tokens']['resident'],body={'house_id':house,'contact_phone':ctx['phones']['resident'],'text':ctx['run_id']+' '+label+' 水龙头滴水，无风险，请普通维修。'},headers={'Idempotency-Key':ctx['run_id']+'-'+label},label='auto-create')
        return label,d.get('id',d.get('work_order',{}).get('id')),client.requests
    finally:client.close()
try:
    for actor in ('resident','staff1','staff2'):
        status,_=c.call('GET','/me',token=ctx['tokens'][actor],expected=(200,401),label=actor+'-session-check')
        if status==401:
            ctx['tokens'][actor]=c.sms_login(ctx['phones'][actor],actor+'-session-renew')['access_token'];save()
    if 'prepared' not in e['steps']:
        if last:
            for label in ('contended5','contended6'):
                d=detail(ctx['orders'][label]);assignment=next(a for a in d['assignments'] if a['status']=='ACTIVE')
                actor=next(k for k,v in ctx['employees'].items() if v==assignment['employee_id'])
                finish(ctx['orders'][label],actor)
            req('POST','/staff/attendance/checkout','staff2',{'property_id':ctx['property_id']})
        if not short:
            prior=('contended1','contended2') if drained else ('parallel1','parallel2')
            for label,actor in zip(prior,('staff1','staff2')):finish(ctx['orders'][label],actor)
            req('POST','/staff/attendance/checkout','staff2',{'property_id':ctx['property_id']})
        else:
            # 24/25 are retained failed-timeout objects, never replay or mutate.
            e['prior_timeout_orders_preserved']=[ctx['orders']['contended3'],ctx['orders']['contended4']]
        e['steps']['prepared']=True;save()
    if 'created' not in e['steps']:
        if drained:
            from celery import Celery
            control=Celery('024-metadata',broker=s.celery_broker_url)
            target='auth001@DESKTOP-8NHSE12'
            for _ in range(10):
                check=control.control.inspect(destination=[target],timeout=2)
                active=check.active();reserved=check.reserved()
                assert active is not None and reserved is not None
                with control.connection_for_read() as conn:
                    count=conn.channel().queue_declare(queue='property_ai.auth001',passive=True).message_count
                if not active[target] and not reserved[target] and count==0:break
                time.sleep(1)
            else:raise RuntimeError('Prior tasks not drained; no further create')
            e['steps']['prelock_idle']={'active':0,'reserved':0,'ready':0};save()
        engine=create_database_engine(s.active_database_url)
        with engine.connect() as lock:
            lock.execute(text('SET SESSION innodb_lock_wait_timeout=15'))
            row=lock.execute(text('SELECT id,employee_no FROM employees WHERE id=:id AND tenant_id=:tenant FOR UPDATE'),{'id':ctx['employees']['staff1'],'tenant':ctx['tenant_id']}).mappings().one()
            assert row['employee_no'].startswith(ctx['run_id'])
            started=time.monotonic()
            with ThreadPoolExecutor(max_workers=2) as pool:
                fs=[pool.submit(create,label,ctx['houses'][house]) for label,house in zip(labels,('a','c'))]
                for f in fs:
                    label,oid,requests=f.result(timeout=10);ctx['orders'][label]=oid;e['requests'].extend(requests);save()
            time.sleep(3 if short else (9 if drained else 5))
            e['steps']['lock_fixture']={'employee':row['id'],'seconds':round(time.monotonic()-started,3),'operation':'SELECT FOR UPDATE then rollback; no DML; only this run synthetic Employee'}
            lock.rollback()
        engine.dispose();e['steps']['created']={l:ctx['orders'][l] for l in labels};save()
    for _ in range(100):
        records=[detail(ctx['orders'][l]) for l in labels]
        if sorted(d['work_order']['status'] for d in records)==['ASSIGNED','PENDING_ASSIGNMENT']:break
        time.sleep(.5)
    assert sorted(d['work_order']['status'] for d in records)==['ASSIGNED','PENDING_ASSIGNMENT']
    assignments=[a for d in records for a in d['assignments'] if a['status']=='ACTIVE']
    assert len(assignments)==1 and assignments[0]['employee_id']==ctx['employees']['staff1']
    e['steps']['one_slot']={'orders':[{'id':d['work_order']['id'],'status':d['work_order']['status']} for d in records],'active_assignments':len(assignments),'auto_no_human_actor':assignments[0]['assigned_by_user_id'] is None};save()
    if 'restored' not in e['steps']:
        req('POST','/staff/attendance/checkin','staff2',{'property_id':ctx['property_id']});e['steps']['restored']=True;save()
    for _ in range(100):
        records=[detail(ctx['orders'][l]) for l in labels]
        if all(d['work_order']['status']=='ASSIGNED' for d in records):break
        time.sleep(.5)
    assert all(d['work_order']['status']=='ASSIGNED' for d in records)
    e['status']='BUSINESS_STATE_PASS_REQUIRE_WORKER_OVERLAP_LOG';save();print(json.dumps(e['steps']))
finally:save();c.close()
