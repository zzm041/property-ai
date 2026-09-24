"""Approved0012 guard/receipt, fresh host backup and additive migration only."""
import argparse
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import re
import site
import subprocess
import sys
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).parent
site.addsitedir(str(ROOT / '.venv/Lib/site-packages'))
sys.path[:0] = [str(ROOT / 'backend'), str(ROOT / 'scripts/ops')]
from auth001_isolated_runtime import build_isolated_settings, apply_isolated_environ
from auth001_recovery_snapshot import validate_target, inspect_container, take_snapshot
from app.core.database import create_database_engine
from sqlalchemy import text, inspect
from celery import Celery

OLD = '20260924_0011_contact_phone'
NEW = '20260925_0012_task_intents'

def write(name, value):
    with (OUT / name).open('x', encoding='utf-8') as f:
        json.dump(value, f, ensure_ascii=False, default=str, indent=2)

def ps(command):
    return subprocess.check_output(['powershell', '-NoProfile', '-Command', command], text=True).strip()

def processes():
    raw = ps("@(Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'python.exe' -and $_.CommandLine -match '(db-task-recovery-025|reliable-task-delivery-026).*(launch-runtime|launch-026)' } | Select-Object ProcessId,ExecutablePath,CommandLine,@{N='Created';E={$_.CreationDate.ToString('o')}}) | ConvertTo-Json -Compress")
    data = json.loads(raw) if raw else []
    return data if isinstance(data, list) else [data]

def receipt(settings):
    validate_target(settings, OLD)  # URL/env guard is identical after additive0012.
    assert settings.celery_broker_url.endswith('/property_ai_auth001')
    assert settings.resolved_celery_queue == 'property_ai.auth001'
    result = {'observed_utc': datetime.now(UTC).isoformat(), 'head': subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
              'database': '127.0.0.1:3307/property_ai_auth001_test', 'processes': processes(), 'container': inspect_container()}
    engine = create_database_engine(settings.active_database_url)
    with engine.connect() as c:
        c.execute(text('SET TRANSACTION READ ONLY'))
        result['revision'] = c.execute(text('SELECT version_num FROM alembic_version')).scalar_one()
        assert result['revision'] in (OLD, NEW)
        tables = c.execute(text("SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA='property_ai_auth001_test' ORDER BY TABLE_NAME")).scalars().all()
        assert all(re.fullmatch('[a-z_]+', t) for t in tables)
        result['counts'] = {t: c.execute(text(f'SELECT COUNT(*) FROM `{t}`')).scalar_one() for t in tables}
        result['protected'] = {}
        for oid in (12,24,25,30,34):
            entries = {}
            for table,col in [('work_orders','id'),('work_order_analyses','work_order_id'),('work_order_assignments','work_order_id'),('work_order_logs','work_order_id'),('ai_call_logs','work_order_id')]:
                rows = [dict(r) for r in c.execute(text(f'SELECT * FROM {table} WHERE {col}=:id ORDER BY id'), {'id':oid}).mappings()]
                entries[table] = {'count':len(rows),'sha256':hashlib.sha256(json.dumps(rows,sort_keys=True,default=str).encode()).hexdigest()}
            result['protected'][str(oid)] = entries
        result['active_other_db_connections'] = c.execute(text("SELECT COUNT(*) FROM information_schema.PROCESSLIST WHERE DB='property_ai_auth001_test' AND ID<>CONNECTION_ID() AND COMMAND<>'Sleep'")).scalar_one()
        if result['revision'] == NEW:
            i = inspect(c)
            result['task_intents_structure'] = {'columns': [r['name'] for r in i.get_columns('task_intents')],
                'indexes': i.get_indexes('task_intents'), 'unique':i.get_unique_constraints('task_intents'),
                'checks':i.get_check_constraints('task_intents'), 'foreign_keys':i.get_foreign_keys('task_intents')}
    engine.dispose()
    result['queue_metadata'] = subprocess.check_output(['docker','exec','property-ai-rabbitmq-1','rabbitmqctl','list_queues','-p','property_ai_auth001','name','messages_ready','messages_unacknowledged','consumers'],text=True).strip()
    result['config_sha256'] = hashlib.sha256((ROOT/'miniprogram/project.config.json').read_bytes()).hexdigest()
    assert result['config_sha256'] == '342ed0b61fe9c74e7a5ce82198c8f36bd9341d5cda83de2c408f33256867fe1f'
    return result

def idle(settings):
    target = 'auth001@DESKTOP-8NHSE12'
    app = Celery('026-quiesce',broker=settings.celery_broker_url)
    inspector = app.control.inspect(destination=[target],timeout=4)
    counts = {}
    for key in ('active','reserved','scheduled'):
        values = getattr(inspector,key)()
        assert values is not None and target in values, 'Worker identity not confirmed'
        counts[key] = len(values[target])
    assert not any(counts.values()), 'Wait for actual active/reserved/scheduled work'
    return app, target, counts

def main():
    p=argparse.ArgumentParser();p.add_argument('action',choices=['inspect','quiesce','backup','migrate']);args=p.parse_args()
    s=build_isolated_settings(enable_enqueue=False)
    before=receipt(s)
    if args.action=='inspect':
        _,_,before['worker_counts']=idle(s)
        write('migration-preflight.json',before)
    elif args.action=='quiesce':
        assert before['revision']==OLD and before['active_other_db_connections']==0
        app,target,before['worker_counts']=idle(s)
        rows=before['processes'];assert len(rows)==2
        expected=str(ROOT/'tmp/uv-python/cpython-3.12.10-windows-x86_64-none/python.exe').lower()
        assert all(r['ExecutablePath'].lower()==expected and 'db-task-recovery-025' in r['CommandLine'] for r in rows)
        api=next(r for r in rows if r['CommandLine'].endswith(' api'))
        worker=next(r for r in rows if r['CommandLine'].endswith(' worker'))
        app.control.broadcast('shutdown', destination=[target])
        deadline=time.monotonic()+30
        while any(r['ProcessId']==worker['ProcessId'] for r in processes()) and time.monotonic()<deadline: time.sleep(.5)
        assert not any(r['ProcessId']==worker['ProcessId'] for r in processes()), 'Worker did not shut down; no forced kill'
        # API is quiesced, no business requests in this step. Windows process
        # termination is explicitly recorded, not mislabelled graceful signal.
        current=next(r for r in processes() if r['ProcessId']==api['ProcessId'])
        assert current==api
        assert receipt(s)['active_other_db_connections']==0
        ps(f"$p=Get-CimInstance Win32_Process -Filter 'ProcessId={api['ProcessId']}'; if ($p.CreationDate.ToString('o') -ne '{api['Created']}') {{throw 'Process changed'}}; Stop-Process -Id {api['ProcessId']} -ErrorAction Stop")
        time.sleep(1)
        after=receipt(s);assert not after['processes'] and after['active_other_db_connections']==0
        write('quiesced.json',{'before':before,'after':after,'mode':'Worker targeted graceful shutdown; idle API Windows process termination after identity/DB activity checks; not a fault-drill kill'})
    elif args.action=='backup':
        assert before['revision']==OLD and not before['processes'] and before['active_other_db_connections']==0
        snap=take_snapshot(s,'rt026-'+uuid4().hex[:12])
        after=receipt(s);assert before['counts']==after['counts'] and before['protected']==after['protected']
        write('fresh-snapshot.json',{'before':before,'snapshot':snap,'after':after})
    else:
        saved=json.loads((OUT/'fresh-snapshot.json').read_text(encoding='utf-8'))
        assert before['revision']==OLD and not before['processes'] and before['active_other_db_connections']==0
        assert before['counts']==saved['after']['counts'] and before['protected']==saved['after']['protected']
        path=Path(saved['snapshot']['private_host_path']);assert hashlib.sha256(path.read_bytes()).hexdigest()==saved['snapshot']['sha256']
        apply_isolated_environ(s)
        from alembic import command
        from alembic.config import Config
        cfg=Config(str(ROOT/'backend/alembic.ini'));cfg.set_main_option('script_location',str(ROOT/'backend/alembic'));cfg.attributes['database_url']=s.active_database_url
        try:
            command.upgrade(cfg,NEW)
            after=receipt(s)
            assert after['revision']==NEW and after['counts']['task_intents']==0
            assert {k:v for k,v in after['counts'].items() if k!='task_intents'}==before['counts']
            assert after['protected']==before['protected']
            assert len(after['task_intents_structure']['columns'])==20
            write('migration-result.json',{'before':before,'after':after,'result':'APPLIED_ONLY_0012_OLD_COUNTS_AND_PROTECTED_UNCHANGED','backup_restore_tested':False})
        except Exception as exc:
            write('migration-error.json',{'error_type':type(exc).__name__,'state':receipt(s),'action':'STOP new feature; retain data/backup; no drop/downgrade/restore'})
            raise RuntimeError('Migration failed; actual state retained') from None
    print(json.dumps({'action':args.action,'result':'recorded','business_requests':0,'model_requests':0}))

if __name__=='__main__':main()
