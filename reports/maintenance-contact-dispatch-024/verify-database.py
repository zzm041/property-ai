"""Read-only evidence for named 024 objects; redact phone before persistence."""
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import re
import runpy
import site
import subprocess

ROOT=Path(__file__).resolve().parents[2]
site.addsitedir(str(ROOT/'.venv/Lib/site-packages'))
h=runpy.run_path(str(ROOT/'scripts/ops/migrate_auth001_contact_024.py'))
from auth001_isolated_runtime import build_isolated_settings
from app.core.database import assert_isolated_auth_database, create_database_engine
from app.core.logging import redact_text
from sqlalchemy import text, bindparam

ctx=json.loads((ROOT/'tmp/maintenance-contact-dispatch-024/context.json').read_text(encoding='utf-8'))
s=build_isolated_settings();assert_isolated_auth_database(s,database_url=s.active_database_url)
engine=create_database_engine(s.active_database_url)
e={'task_id':'MAINTENANCE-CONTACT-DISPATCH-024','run_id':ctx['run_id'],'captured_at_utc':datetime.now(UTC).isoformat(),'mode':'read-only SQL, exact isolated database; no business writes','orders':ctx['orders']}
with engine.connect() as c:
    c.execute(text('SET TRANSACTION READ ONLY'))
    snap=h['snapshot'](c)
    assert snap['revision']==['20260924_0011_contact_phone']
    assert snap['order12_hash_excluding_new_column']=='b2fedaa66d1341fe3c027964f058862b7ab51ccfc424cf23b9b7d4223119ab89'
    e['schema_and_protected_order']=snap
    e['old_contact_nonnull_count']=c.execute(text('SELECT COUNT(*) FROM work_orders WHERE id<=12 AND contact_phone_e164 IS NOT NULL')).scalar_one()
    assert e['old_contact_nonnull_count']==0
    ids=sorted(ctx['orders'].values())
    def rows(sql,values=ids):
        return [dict(row) for row in c.execute(text(sql).bindparams(bindparam('ids',expanding=True)),{'ids':values}).mappings()]
    e['work_orders']=rows('SELECT id,tenant_id,property_id,reporter_user_id,status,version,current_assignment_id,created_at,closed_at FROM work_orders WHERE id IN :ids ORDER BY id')
    phones=rows('SELECT id,contact_phone_e164 FROM work_orders WHERE id IN :ids')
    canonical=lambda value:'+86'+value.removeprefix('+86')
    original=canonical(ctx['original_resident_phone']);current=canonical(ctx['phones']['resident'])
    e['snapshot_checks']=[{'order_id':row['id'],'collected':bool(row['contact_phone_e164']),
        'matches_original_before_rebind':row['contact_phone_e164']==original,
        'matches_current_after_rebind':row['contact_phone_e164']==current,
        'matches_explicit_alternate':row['contact_phone_e164']==canonical(ctx['phones']['staff1'])} for row in phones]
    e['assignments']=rows('SELECT id,work_order_id,employee_id,assigned_by_user_id,assigned_source,status,assigned_at,ended_at,reason FROM work_order_assignments WHERE work_order_id IN :ids ORDER BY id')
    assert all(r['assigned_source']=='AUTO' and r['assigned_by_user_id'] is None for r in e['assignments'])
    e['analysis']=rows('SELECT id,work_order_id,analysis_version,status,category,required_role,required_skill,prompt_version FROM work_order_analyses WHERE work_order_id IN :ids ORDER BY id')
    e['history']=rows('SELECT id,work_order_id,event_code,from_status,to_status,request_id,payload_json FROM work_order_logs WHERE work_order_id IN :ids ORDER BY id')
    e['receipts']=rows('SELECT r.id,p.work_order_id,p.assignee_id,r.problem_type,r.method,r.resolved FROM work_order_receipts r JOIN work_order_processes p ON r.process_id=p.id WHERE p.work_order_id IN :ids ORDER BY r.id')
    e['notifications']=rows("SELECT n.id,n.resource_id,n.user_id,d.status,d.attempt,d.channel FROM notifications n LEFT JOIN notification_deliveries d ON d.notification_id=n.id WHERE n.resource_type='WORK_ORDER' AND n.resource_id IN :ids ORDER BY n.id")
    e['contact_audits']=rows("SELECT id,actor_user_id,resource_id,tenant_id,property_id,details_json FROM audit_logs WHERE action='WORK_ORDER_CONTACT_READ' AND resource_id IN :ids ORDER BY id")
    e['employees']=rows('SELECT id,user_id,tenant_id,status,max_capacity FROM employees WHERE id IN :ids ORDER BY id',list(ctx['employees'].values()))
    e['skill_counts']=rows('SELECT e.id,COUNT(s.skill_id) AS skill_count FROM employees e LEFT JOIN employee_skills s ON s.employee_id=e.id WHERE e.id IN :ids GROUP BY e.id',list(ctx['employees'].values()))
    e['appointments']=rows('SELECT id,employee_id,property_id,status,valid_from,valid_to FROM employee_properties WHERE employee_id IN :ids ORDER BY id',list(ctx['employees'].values()))
engine.dispose()
e['runtime']={}
for name in ('api-03','worker-04','worker-05'):
    content=(ROOT/f'tmp/maintenance-contact-dispatch-024/{name}.stdout.log').read_text(encoding='utf-8')
    e['runtime'][name]=next(json.loads(line) for line in content.splitlines() if line.startswith('{"task_id"'))
    e['runtime'][name]['observed_threads_2']='concurrency: 2 (thread)' in content
    e['runtime'][name]['observed_threads_4']='concurrency: 4 (thread)' in content
assert e['runtime']['api-03']['backend_app_wip_diff_sha256']==e['runtime']['worker-04']['backend_app_wip_diff_sha256']
phone_values=list(ctx['phones'].values())+[ctx['original_resident_phone']]
serialized=json.dumps(e,ensure_ascii=False,default=str,indent=2)
assert all(value.removeprefix('+86') not in serialized for value in phone_values),'Evidence must not contain full telephone'
Path(__file__).with_name('database-evidence.json').write_text(serialized,encoding='utf-8')
worker=[]
for name in ('worker-01','worker-02','worker-03','worker-04','worker-05'):
    for stream in ('stdout','stderr'):
        for line in (ROOT/f'tmp/maintenance-contact-dispatch-024/{name}.{stream}.log').read_text(encoding='utf-8').splitlines():
            if not any(k in line for k in ('auto_dispatch','auto_redispatch','retry_waiting_dispatch_task[','analyze_work_order_task[','concurrency:','"task_id": "MAINTENANCE','celery real task signal','failed_task_recorded','celery_task_failed')):continue
            line=redact_text(line)
            for number in phone_values:line=line.replace(number.removeprefix('+86'),'[redacted-phone]')
            worker.append(f'{name}.{stream}: {line}')
Path(__file__).with_name('worker-events.log').write_text('\n'.join(worker)+'\n',encoding='utf-8')
raw=(ROOT/'tmp/maintenance-contact-dispatch-024/worker-05.stderr.log').read_text(encoding='utf-8')
signals=[];mapping={}
for line in raw.splitlines():
    if '{"source": "celery real task signal"' in line:
        value=json.loads(line[line.index('{"source"'):])
        if value['task_name']=='app.worker.tasks.analyze_work_order_task':signals.append(value)
    match=re.search(r'task_id=([\w-]+) work_order_id=(\d+)',line)
    if match:mapping[match[1]]=int(match[2])
groups={}
for signal in signals:
    groups.setdefault(signal['task_id'],{})[signal['event']]=signal
intervals=[]
for task,events in groups.items():
    intervals.append({'task_id':task,'work_order_id':mapping[task],'thread_id':events['task_prerun']['thread_id'],
                      'start':events['task_prerun']['observed_utc'],'end':events['task_postrun']['observed_utc']})
assert len(intervals)==2 and intervals[0]['thread_id']!=intervals[1]['thread_id']
overlap=(min(datetime.fromisoformat(x['end']) for x in intervals)-max(datetime.fromisoformat(x['start']) for x in intervals)).total_seconds()
assert overlap>0
Path(__file__).with_name('parallel-overlap.json').write_text(json.dumps({'source':'real Celery prerun/postrun metadata correlated with AUTO outcome logs','intervals':intervals,'overlap_seconds':overlap,'business_result_file':'contended-dispatch-four.json','same_worker_source_as_api':e['runtime']['worker-05']['backend_app_wip_diff_sha256']==e['runtime']['api-03']['backend_app_wip_diff_sha256']},indent=2),encoding='utf-8')
failure=(ROOT/'tmp/maintenance-contact-dispatch-024/worker-04.stderr.log').read_text(encoding='utf-8')
lines=failure.splitlines();start=next(i for i,line in enumerate(lines) if '[error    ] auto_dispatch_failed' in line)
end=next(i for i,line in enumerate(lines) if line.startswith('[2026-09-25 00:22:'))
failure=redact_text('\n'.join(lines[start:end]))
for number in phone_values:failure=failure.replace(number.removeprefix('+86'),'[redacted-phone]')
Path(__file__).with_name('fault-injection-2013.log').write_text(failure+'\n',encoding='utf-8')
print(json.dumps({'run_id':ctx['run_id'],'orders':len(ids),'all_assignments_AUTO':len(e['assignments']),'order12_unchanged':True,'old_contact_null':True,'same_runtime_code_hash':True,'phones_redacted':True}))
