"""Final read-only bindings; close counters without resetting or replaying."""
import sys
from pathlib import Path
import importlib.util
sys.path.insert(0,str(Path(__file__).parent))
from common026 import *
spec=importlib.util.spec_from_file_location('environment026',OUT/'environment-026.py')
env=importlib.util.module_from_spec(spec);spec.loader.exec_module(env)
ctx=context();assert 'drill_finished' not in ctx
elapsed=(datetime.now(UTC)-datetime.fromisoformat(ctx['drill_started'])).total_seconds();assert elapsed<=900
before=json.loads((OUT/'fresh-snapshot.json').read_text(encoding='utf-8'))['before']
receipt=env.receipt(settings());assert receipt['protected']==before['protected']
_,_,receipt['worker_counts']=env.idle(settings());assert not any(receipt['worker_counts'].values())
assert 'property_ai.auth001\t0\t0\t1' in receipt['queue_metadata']
assert 'property_ai.auth001.dlq\t7\t0\t0' in receipt['queue_metadata']
engine=create_database_engine(settings().active_database_url)
with engine.connect() as conn:
    conn.execute(text('SET TRANSACTION READ ONLY'))
    receipt['final_work']=[dict(r) for oid in ctx['orders'].values() for r in conn.execute(text('SELECT id,status,version,current_assignment_id FROM work_orders WHERE id=:id'),{'id':oid}).mappings()]
    receipt['knowledge']=[dict(r) for r in conn.execute(text('SELECT d.id,d.status,d.current_version,v.version,v.status AS version_status,j.id AS job_id,j.attempt,j.status AS job_status FROM knowledge_documents d JOIN knowledge_document_versions v ON v.document_id=d.id JOIN knowledge_ingestion_jobs j ON j.document_id=d.id AND j.version=v.version WHERE d.id=:id'),{'id':ctx['knowledge']['document_id']}).mappings()]
    receipt['knowledge_chunks']=[dict(r) for r in conn.execute(text('SELECT id,document_id,version,tenant_id,property_id,visibility,vector_id FROM knowledge_chunks WHERE document_id=:id'),{'id':ctx['knowledge']['document_id']}).mappings()]
    receipt['load']=[dict(r) for r in conn.execute(text('SELECT business_date,current_load FROM employee_daily_statuses WHERE employee_id=:id ORDER BY business_date'),{'id':ctx['employee_id']}).mappings()]
engine.dispose()
logs=[]
for label,record in ctx['processes'].items():
    for channel in ('stdout','stderr'):
        path=PRIVATE/(label+'.'+channel+'.log')
        for line in path.read_text(encoding='utf-8').splitlines():
            try:value=json.loads(line)
            except (ValueError,TypeError):continue
            logs.append({'label':label,'stream':channel,'source':'actual process JSON log','record':value})
write('process-events.json',logs)
receipt.update(run_id=ctx['run_id'],elapsed_seconds=round(elapsed,3),terminations=ctx['kills'],process_records=ctx['processes'],source_sha256=source_hash(),model_requests=0,new_orders=len(ctx['orders']),new_knowledge_versions=1,restore_validated=False)
write('runtime-final.json',receipt)
ctx['drill_finished']=stamp();ctx['elapsed_seconds']=round(elapsed,3);save_context(ctx)
(PRIVATE/'intent-ids.json').write_text('[]',encoding='utf-8')
print(json.dumps({'run_id':ctx['run_id'],'elapsed_seconds':round(elapsed,2),'orders':ctx['orders'],'revision':receipt['revision'],'queue_metadata':receipt['queue_metadata'],'protected_unchanged':True}))
