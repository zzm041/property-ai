"""Scoped026 helpers; no I/O beyond dependency/settings bootstrap at import."""
from datetime import UTC,datetime
import hashlib
import json
from pathlib import Path
import runpy
import site
import sys
ROOT=Path(__file__).resolve().parents[2]
OUT=Path(__file__).parent
PRIVATE=ROOT/'tmp/reliable-task-delivery-026/runtime'
site.addsitedir(str(ROOT/'.venv/Lib/site-packages'))
sys.path[:0]=[str(ROOT/'backend'),str(ROOT/'scripts/ops')]
from auth001_isolated_runtime import build_isolated_settings,apply_isolated_environ
from app.core.database import create_database_engine
from sqlalchemy import text

def stamp():return datetime.now(UTC).isoformat()
def settings():
    s=build_isolated_settings(enable_enqueue=True).model_copy(update={'durable_tasks_enabled':True})
    assert s.celery_broker_url.endswith('/property_ai_auth001') and s.resolved_celery_queue=='property_ai.auth001'
    return s
def context():return json.loads((PRIVATE/'context.json').read_text(encoding='utf-8'))
def save_context(value):
    PRIVATE.mkdir(parents=True,exist_ok=True)
    (PRIVATE/'context.json').write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
def write(name,value,exclusive=True):
    raw=json.dumps(value,ensure_ascii=False,default=str,indent=2)
    if (PRIVATE/'context.json').exists():
        ctx=context()
        for phone in ctx.get('phones',{}).values():assert phone.removeprefix('+86') not in raw
        for token in ctx.get('tokens',{}).values():assert token not in raw
    with (OUT/name).open('x' if exclusive else 'w',encoding='utf-8') as f:f.write(raw)
def source_hash():
    entries=[(str(p.relative_to(ROOT)).replace('\\','/'),hashlib.sha256(p.read_bytes().replace(b'\r\n',b'\n')).hexdigest()) for p in sorted((ROOT/'backend/app').rglob('*.py'))]
    return hashlib.sha256(json.dumps(entries).encode()).hexdigest()
def guard(s):
    from app.core.database import assert_isolated_auth_database
    assert_isolated_auth_database(s,database_url=s.active_database_url)
    engine=create_database_engine(s.active_database_url)
    with engine.connect() as c:assert c.execute(text('SELECT version_num FROM alembic_version')).scalar_one()=='20260925_0012_task_intents'
    engine.dispose()
def client(run):
    return runpy.run_path(str(ROOT/'reports/notification-events-inbox-013/run-runtime.py'))['Client'](run)
