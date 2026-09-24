"""Exact new-run readonly verification; no message access or business writes."""
import json
from pathlib import Path
import re
import sys
sys.path.insert(0,str(Path(__file__).parent))
from common import *
from sqlalchemy import text
from app.core.logging import redact_text
from app.worker.failed_tasks import get_failed_task

ctx=read_context();s=settings();folder=Path(__file__).parent
before=json.loads((folder/'before.json').read_text(encoding='utf-8'))
e={'task_id':'DB-TASK-RECOVERY-025','run_id':ctx['run_id'],'observed_utc':stamp(),
   'mode':'exact-run readonly DB; exact new failed-task record; no DLQ message read',
   'source_sha256':source_hash(),'orders':{}}
engine=create_database_engine(s.active_database_url)
with engine.connect() as conn:
    conn.execute(text('SET TRANSACTION READ ONLY'))
    e['revision']=conn.execute(text('SELECT version_num FROM alembic_version')).scalar_one()
    assert e['revision']=='20260924_0011_contact_phone'
    e['protected']=protected_snapshot(conn)
    assert e['protected']==before['protected']
    e['protected_12_24_25_unchanged']=True
    def query(sql,params):
        result=[dict(x) for x in conn.execute(text(sql),params).mappings()]
        for row in result:
            for key,value in row.items():
                if key.endswith('_json') and isinstance(value,str):row[key]=json.loads(value)
        return result
    for label,oid in ctx['orders'].items():
        p={'id':oid}
        r={'order':query('SELECT id,tenant_id,property_id,reporter_user_id,status,version,current_assignment_id FROM work_orders WHERE id=:id',p)[0]}
        r['analyses']=query('SELECT id,analysis_version,status,input_hash,error_code FROM work_order_analyses WHERE work_order_id=:id ORDER BY id',p)
        r['calls']=query('SELECT id,provider_type,attempt,status,model_version FROM ai_call_logs WHERE work_order_id=:id ORDER BY id',p)
        r['assignments']=query('SELECT id,employee_id,status,assigned_source,assigned_by_user_id FROM work_order_assignments WHERE work_order_id=:id ORDER BY id',p)
        r['logs']=query('SELECT id,event_code,from_status,to_status,actor_user_id,payload_json,created_at FROM work_order_logs WHERE work_order_id=:id ORDER BY id',p)
        r['notifications']=query("SELECT id,user_id,title,resource_type,resource_id FROM notifications WHERE resource_type='WORK_ORDER' AND resource_id=:id ORDER BY id",p)
        r['deliveries']=query("SELECT d.id,d.notification_id,d.status,d.attempt,d.last_error_code FROM notification_deliveries d JOIN notifications n ON n.id=d.notification_id WHERE n.resource_type='WORK_ORDER' AND n.resource_id=:id ORDER BY d.id",p)
        r['audits']=query("SELECT id,actor_user_id,action,resource_type,resource_id,details_json,created_at FROM audit_logs WHERE property_id=:property AND ((resource_type='WORK_ORDER' AND resource_id=:id) OR (resource_type='WORK_ORDER_ANALYSIS' AND resource_id IN (SELECT id FROM work_order_analyses WHERE work_order_id=:id)) OR (resource_type='WORK_ORDER_ASSIGNMENT' AND resource_id IN (SELECT id FROM work_order_assignments WHERE work_order_id=:id))) ORDER BY id",{**p,'property':ctx['property_id']})
        phone=conn.execute(text('SELECT contact_phone_e164 FROM work_orders WHERE id=:id'),p).scalar_one()
        r['contact_snapshot_matches']=phone=='+86'+ctx['phones']['resident'].removeprefix('+86')
        assert r['contact_snapshot_matches']
        e['orders'][label]=r
    e['employee_load']=query('SELECT employee_id,property_id,business_date,current_load FROM employee_daily_statuses WHERE employee_id=:id ORDER BY business_date,property_id',{'id':ctx['employee_id']})
engine.dispose()
for label in ('dispatch','busy','commit'):
    r=e['orders'][label]
    assert r['order']['status']=='CLOSED' and len(r['assignments'])==1 and len(r['calls'])==1
    assert r['assignments'][0]['assigned_source']=='AUTO'
assert e['orders']['cancel']['order']['status']=='CANCELLED'
assert not e['orders']['cancel']['calls'] and not e['orders']['cancel']['assignments']
failed=next(x['payload_json'] for x in e['orders']['persistent']['logs'] if x['event_code']=='AI_TASK_FAILED')
assert failed['attempt']==3 and failed['error_code']=='AI.DATABASE_RETRY_EXHAUSTED'
record=get_failed_task(failed['task_id'],s)
assert record and record['status']=='RECORDED' and not record['replayed']
e['new_exhaustion_record']={k:record.get(k) for k in ('record_id','task_id','operation','error_code','recorded_at','queue','dlq','status','replayed','current_attempt')}
e['new_exhaustion_record']['current_attempt_meaning']='explicit replay count, not automatic Celery attempt (3)'
assert all(x['current_load']==0 for x in e['employee_load'])
public_write('database-final.json',e)

# Preserve both unsuccessful and successful raw streams. Scrub before public disk.
manifest=[]
for name in ('api-01.stdout','api-01.stderr','worker-01.stdout','worker-01.stderr','worker-02.stdout','worker-02.stderr','worker-03.stdout','worker-03.stderr'):
    source=ROOT/'tmp/db-task-recovery-025'/f'{name}.log'
    raw=source.read_text(encoding='utf-8',errors='replace')
    safe=redact_text(raw)
    for phone in ctx['phones'].values():safe=safe.replace(phone,'[SYNTHETIC_PHONE_REDACTED]').replace(phone.removeprefix('+86'),'[SYNTHETIC_PHONE_REDACTED]')
    for token in ctx['tokens'].values():safe=safe.replace(token,'[SESSION_REDACTED]')
    assert not re.search(r'(?<!\d)1[3-9]\d{9}(?!\d)',safe),'phone-like raw content requires review'
    output=folder/(name+'.txt');output.write_text(safe,encoding='utf-8')
    manifest.append({'source':str(source.relative_to(ROOT)),'public':output.name,'source_sha256':hashlib.sha256(raw.encode()).hexdigest(),'public_sha256':hashlib.sha256(safe.encode()).hexdigest(),'scrubbing':'product redact_text + exact synthetic phones and session tokens'})
public_write('log-manifest.json',manifest)
print(json.dumps({'protected_unchanged':True,'orders':{k:{'id':v['order']['id'],'status':v['order']['status'],'analysis':len(v['analyses']),'calls':len(v['calls']),'assignments':len(v['assignments']),'notifications':len(v['notifications']),'audits':len(v['audits'])} for k,v in e['orders'].items()},'final_load':e['employee_load'],'raw_logs_saved':len(manifest)},ensure_ascii=False,default=str))
