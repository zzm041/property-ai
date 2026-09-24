"""025 shared evidence helpers; importing performs no business operation."""
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import runpy
import site
import sys

ROOT=Path(__file__).resolve().parents[2]
site.addsitedir(str(ROOT/'.venv/Lib/site-packages'))
sys.path[:0]=[str(ROOT/'backend'),str(ROOT/'scripts/ops')]
helpers=runpy.run_path(str(ROOT/'reports/notification-events-inbox-013/run-runtime.py'))
Client=helpers['Client']
PRIVATE=ROOT/'tmp/db-task-recovery-025/context.json'
from auth001_isolated_runtime import build_isolated_settings,apply_isolated_environ
from app.core.database import assert_isolated_auth_database,create_database_engine

def settings():
    s=build_isolated_settings(enable_enqueue=True)
    assert_isolated_auth_database(s,database_url=s.active_database_url)
    assert s.celery_task_queue=='property_ai.auth001'
    assert s.celery_broker_url.endswith('/property_ai_auth001')
    return s

def stamp():return datetime.now(UTC).isoformat()
def read_context():return json.loads(PRIVATE.read_text(encoding='utf-8'))
def save_context(ctx):
    PRIVATE.parent.mkdir(parents=True,exist_ok=True)
    PRIVATE.write_text(json.dumps(ctx,ensure_ascii=False,indent=2),encoding='utf-8')
def public_write(name,payload):
    value=json.dumps(payload,ensure_ascii=False,default=str,indent=2)
    if PRIVATE.exists():
        ctx=read_context()
        for phone in ctx.get('phones',{}).values():
            assert phone.removeprefix('+86') not in value,'private phone in evidence'
        for token in ctx.get('tokens',{}).values():assert token not in value,'private token in evidence'
    (Path(__file__).parent/name).write_text(value,encoding='utf-8')
def source_hash():
    entries=[(str(p.relative_to(ROOT)).replace('\\','/'),hashlib.sha256(p.read_bytes().replace(b'\r\n',b'\n')).hexdigest()) for p in sorted((ROOT/'backend/app').rglob('*.py'))]
    return hashlib.sha256(json.dumps(entries).encode()).hexdigest()

def protected_snapshot(connection):
    from sqlalchemy import text
    result={}
    for oid in (12,24,25):
        tables={}
        for table,column in [('work_orders','id'),('work_order_analyses','work_order_id'),
                             ('work_order_assignments','work_order_id'),('work_order_logs','work_order_id'),
                             ('ai_call_logs','work_order_id')]:
            rows=[dict(r) for r in connection.execute(text(f'SELECT * FROM {table} WHERE {column}=:oid ORDER BY id'),{'oid':oid}).mappings()]
            tables[table]={'count':len(rows),'sha256':hashlib.sha256(json.dumps(rows,sort_keys=True,default=str).encode()).hexdigest()}
        result[str(oid)]=tables
    return result
