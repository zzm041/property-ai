"""Readonly exact environment + immutable protected-object fingerprints."""
import json
from pathlib import Path
import subprocess
import sys
sys.path.insert(0,str(Path(__file__).parent))
from common import *
from sqlalchemy import text
from celery import Celery

s=settings(); e={'task_id':'DB-TASK-RECOVERY-025','observed_utc':stamp(),'target':'127.0.0.1:3307/property_ai_auth001_test','mode':'readonly'}
engine=create_database_engine(s.active_database_url)
with engine.connect() as c:
    c.execute(text('SET TRANSACTION READ ONLY'))
    e['revision']=c.execute(text('SELECT version_num FROM alembic_version')).scalar_one()
    assert e['revision']=='20260924_0011_contact_phone'
    e['protected']=protected_snapshot(c)
engine.dispose()
control=Celery('025-preflight',broker=s.celery_broker_url)
target='auth001@DESKTOP-8NHSE12'; inspect=control.control.inspect(destination=[target],timeout=4)
e['worker_task_counts']={}
for name in ('active','reserved','scheduled'):
    result=getattr(inspect,name)(); assert result is not None and target in result
    e['worker_task_counts'][name]=len(result[target])
e['vhost']='property_ai_auth001';e['queue']='property_ai.auth001'
e['queue_metadata']=subprocess.check_output(['docker','exec','property-ai-rabbitmq-1','rabbitmqctl','list_queues','-p','property_ai_auth001','name','messages_ready','messages_unacknowledged','consumers'],text=True).strip()
e['head']=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()
e['processes']=json.loads(subprocess.check_output(['powershell','-NoProfile','-Command',
    "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*reports\\db-task-recovery-025\\launch-runtime.py*' -and $_.Name -eq 'python.exe' } | Select-Object ProcessId,ExecutablePath,CommandLine | ConvertTo-Json"],text=True))
e['protected_config_sha256']=__import__('hashlib').sha256((ROOT/'miniprogram/project.config.json').read_bytes()).hexdigest()
public_write(sys.argv[1]+'.json',e)
print(json.dumps(e,ensure_ascii=False,indent=2))
