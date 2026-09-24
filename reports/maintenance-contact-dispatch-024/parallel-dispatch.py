"""Two real worker task threads + concurrent ordinary API submissions.

Only normal attendance and create APIs; no analysis/assignment script or SQL
state changes. Worker runs same approved Development source and isolated queue.
"""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import runpy
import time

ROOT=Path(__file__).resolve().parents[2]
h=runpy.run_path(str(ROOT/'reports/notification-events-inbox-013/run-runtime.py'))
private=ROOT/'tmp/maintenance-contact-dispatch-024/context.json'
ctx=json.loads(private.read_text(encoding='utf-8'))
out=Path(__file__).with_name('parallel-dispatch.json')
e=json.loads(out.read_text(encoding='utf-8')) if out.exists() else {'run_id':ctx['run_id'],'steps':{},'requests':[],'worker_pool':'threads concurrency=2, same AUTH001 worker/queue/vhost/source'}
c=h['Client'](ctx['run_id']+'-parallel')
def save():
    e['requests'].extend(c.requests);c.requests.clear()
    out.write_text(json.dumps(e,ensure_ascii=False,indent=2),encoding='utf-8')
    private.write_text(json.dumps(ctx,ensure_ascii=False,indent=2),encoding='utf-8')
def call(method,path,actor,body=None,label='parallel'):
    return c.data(method,path,token=ctx['tokens'][actor],body=body,label=label)
try:
    if 'off_duty' not in e['steps']:
        call('POST','/staff/attendance/checkout','staff2',{'property_id':ctx['property_id']},'second-checkout')
        e['steps']['off_duty']=True;save()
    if 'created' not in e['steps']:
        def create(label,house):
            client=h['Client'](ctx['run_id']+'-'+label)
            try:
                d=client.data('POST','/work-orders',token=ctx['tokens']['resident'],body={'house_id':house,'text':ctx['run_id']+' '+label+' 水龙头漏水，需要普通维修，无安全风险。','contact_phone':ctx['phones']['resident']},headers={'Idempotency-Key':ctx['run_id']+'-'+label},label='two-worker-create')
                return label,d.get('id',d.get('work_order',{}).get('id')),client.requests
            finally:client.close()
        with ThreadPoolExecutor(max_workers=2) as executor:
            fs=[executor.submit(create,label,ctx['houses'][house]) for label,house in [('parallel1','a'),('parallel2','c')]]
            for f in fs:
                label,oid,requests=f.result();ctx['orders'][label]=oid;e['requests'].extend(requests);save()
        e['steps']['created']={label:ctx['orders'][label] for label in ('parallel1','parallel2')};save()
    for _ in range(100):
        records=[call('GET',f"/work-orders/{ctx['orders'][label]}",'resident',label='inspect-'+label) for label in ('parallel1','parallel2')]
        states=sorted(d['work_order']['status'] for d in records)
        if states==['ASSIGNED','PENDING_ASSIGNMENT']:break
        time.sleep(.5)
    assert states==['ASSIGNED','PENDING_ASSIGNMENT'],states
    assignments=[a for d in records for a in d['assignments'] if a['status']=='ACTIVE']
    assert len(assignments)==1 and assignments[0]['employee_id']==ctx['employees']['staff1']
    assert assignments[0]['assigned_by_user_id'] is None
    e['steps']['single_available_slot']={'orders':[{'id':d['work_order']['id'],'status':d['work_order']['status']} for d in records],
                                      'active_assignments':len(assignments),'auto_employee':assignments[0]['employee_id']}
    save()
    # Restore availability through the same normal API, not database stamping.
    if 'restored' not in e['steps']:
        call('POST','/staff/attendance/checkin','staff2',{'property_id':ctx['property_id']},'second-checkin')
        e['steps']['restored']=True;save()
    for _ in range(100):
        records=[call('GET',f"/work-orders/{ctx['orders'][label]}",'resident',label='final-'+label) for label in ('parallel1','parallel2')]
        if all(d['work_order']['status']=='ASSIGNED' for d in records):break
        time.sleep(.5)
    assert all(d['work_order']['status']=='ASSIGNED' for d in records)
    employees=[next(a['employee_id'] for a in d['assignments'] if a['status']=='ACTIVE') for d in records]
    assert sorted(employees)==sorted(ctx['employees'].values())
    e['steps']['availability_auto_resume']={'orders':[d['work_order']['id'] for d in records],'distinct_employees':employees}
    e['status']='PASS_REAL_PARALLEL_WORKER_SOFTWARE';save();print(json.dumps(e['steps']))
finally:save();c.close()
