"""Readonly026 receipt; no business requests, scans, message reads or tasks."""
import hashlib
import json
from pathlib import Path
import site
import subprocess
import sys
from datetime import UTC,datetime

ROOT=Path(__file__).resolve().parents[2]
site.addsitedir(str(ROOT/'.venv/Lib/site-packages'))
sys.path[:0]=[str(ROOT/'backend'),str(ROOT/'scripts/ops')]
from auth001_isolated_runtime import build_isolated_settings
from auth001_recovery_snapshot import validate_target,inspect_container
from app.core.database import create_database_engine
from sqlalchemy import text
from celery import Celery

s=build_isolated_settings(enable_enqueue=False);validate_target(s,'20260924_0011_contact_phone')
assert s.celery_broker_url.endswith('/property_ai_auth001') and s.resolved_celery_queue=='property_ai.auth001'
e={'task_id':'RELIABLE-TASK-DELIVERY-026','observed_utc':datetime.now(UTC).isoformat(),
   'head':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),'mode':'readonly'}
engine=create_database_engine(s.active_database_url)
with engine.connect() as c:
    c.execute(text('SET TRANSACTION READ ONLY'))
    e['revision']=c.execute(text('SELECT version_num FROM alembic_version')).scalar_one();validate_target(s,e['revision'])
    e['tables']=[dict(r) for r in c.execute(text("SELECT TABLE_NAME,ENGINE FROM information_schema.TABLES WHERE TABLE_SCHEMA='property_ai_auth001_test' AND (TABLE_NAME LIKE '%job%' OR TABLE_NAME LIKE '%outbox%' OR TABLE_NAME LIKE '%intent%' OR TABLE_NAME IN ('notification_deliveries','work_order_analyses','work_order_logs')) ORDER BY TABLE_NAME")).mappings()]
    e['protected']={}
    for oid in (12,24,25,30,34):
        entries={}
        for table,col in [('work_orders','id'),('work_order_analyses','work_order_id'),('work_order_assignments','work_order_id'),('work_order_logs','work_order_id'),('ai_call_logs','work_order_id')]:
            rows=[dict(r) for r in c.execute(text(f'SELECT * FROM {table} WHERE {col}=:id ORDER BY id'),{'id':oid}).mappings()]
            entries[table]={'count':len(rows),'sha256':hashlib.sha256(json.dumps(rows,sort_keys=True,default=str).encode()).hexdigest()}
        e['protected'][str(oid)]=entries
engine.dispose()
e['container']=inspect_container()
e['processes']=json.loads(subprocess.check_output(['powershell','-NoProfile','-Command',"Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'python.exe' -and $_.CommandLine -like '*reports\\db-task-recovery-025\\launch-runtime.py*' } | Select-Object ProcessId,ExecutablePath,CommandLine | ConvertTo-Json"],text=True))
e['startup_records']=[json.loads((ROOT/f'tmp/db-task-recovery-025/{name}.stdout.log').read_text(encoding='utf-8').splitlines()[0]) for name in ('api-01','worker-03')]
entries=[(str(p.relative_to(ROOT)).replace('\\','/'),hashlib.sha256(p.read_bytes().replace(b'\r\n',b'\n')).hexdigest()) for p in sorted((ROOT/'backend/app').rglob('*.py'))]
e['current_source_sha256']=hashlib.sha256(json.dumps(entries).encode()).hexdigest()
assert all(r['source_sha256']==e['current_source_sha256'] for r in e['startup_records'])
assert {r['pid'] for r in e['startup_records']}=={r['ProcessId'] for r in e['processes']}
target='auth001@DESKTOP-8NHSE12';inspect=Celery('026-readonly',broker=s.celery_broker_url).control.inspect(destination=[target],timeout=4)
e['worker_counts']={}
for key in ('active','reserved','scheduled'):
    result=getattr(inspect,key)();assert result is not None and target in result
    e['worker_counts'][key]=len(result[target])
e['queue_metadata']=subprocess.check_output(['docker','exec','property-ai-rabbitmq-1','rabbitmqctl','list_queues','-p','property_ai_auth001','name','messages_ready','messages_unacknowledged','consumers'],text=True).strip()
e['protected_config_sha256']=hashlib.sha256((ROOT/'miniprogram/project.config.json').read_bytes()).hexdigest()
e['scope']='Only AUTH0010011 metadata/explicit five protected objects; queue metadata only, no DLQ message access; no restart'
(Path(__file__).parent/'receipt.json').write_text(json.dumps(e,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'head':e['head'],'revision':e['revision'],'same_source':True,'worker_counts':e['worker_counts'],'queue_metadata':e['queue_metadata']},ensure_ascii=False))
