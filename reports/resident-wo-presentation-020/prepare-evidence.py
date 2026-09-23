"""Only SELECT current synthetic objects and unique-worker idle metadata; no business write."""
from pathlib import Path
from datetime import UTC, datetime
import json
import runpy
import secrets
import subprocess

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).parent
PRIVATE = ROOT / 'tmp/resident-wo-presentation-020/context.json'
h = runpy.run_path(str(ROOT / 'reports/notification-events-inbox-013/run-runtime.py'))
settings = h['_guard']()
from sqlalchemy import create_engine, text
from auth001_isolated_runtime import apply_isolated_environ
apply_isolated_environ(settings)
from app.worker.celery_app import celery_app

assert not (OUT/'preflight.json').exists(), 'Keep existing checkpoint; no preflight replay'
prior = json.loads((ROOT/'tmp/ai-analysis-input-019/context.json').read_text(encoding='utf-8'))
history = json.loads((ROOT/'tmp/wo-reopen-receipt-history-014/context.json').read_text(encoding='utf-8'))
PRIVATE.parent.mkdir(parents=True, exist_ok=True)
ctx = json.loads(PRIVATE.read_text(encoding='utf-8')) if PRIVATE.exists() else {
    'run_id': 'view020-'+secrets.token_hex(6), 'last_sms_sent_at': {},
    **{k:prior[k] for k in ('phones','identities','employees','tenant_id','property_id','house_id')},
    'history_order_id': history['page_order_id'], 'active_order_id': prior['api_order_id'],
    'source_runs': [history['run_id'], prior['run_id']],
}
PRIVATE.write_text(json.dumps(ctx,ensure_ascii=False,indent=2),encoding='utf-8')
engine = create_engine(settings.test_database_url)
e = {'task_id':'RESIDENT-WO-PRESENTATION-020','run_id':ctx['run_id'],'product_sha':'b75fa97112b3144f9d4702e4782b231bb775c19c',
     'source_runs':ctx['source_runs'],'database':'127.0.0.1:3307/property_ai_auth001_test','mode':'SELECT-only current restored synthetic objects; no lifecycle replay'}
try:
    with engine.connect() as c:
        c.execute(text('SET TRANSACTION READ ONLY'))
        e['revision'] = c.execute(text('SELECT version_num FROM alembic_version')).scalar_one()
        assert e['revision'] == '20260919_0009'
        params={'history':ctx['history_order_id'],'active':ctx['active_order_id']}
        e['orders']=[dict(r) for r in c.execute(text('SELECT id,tenant_id,property_id,reporter_user_id,status,version FROM work_orders WHERE id IN (:history,:active) ORDER BY id'),params).mappings()]
        assert len(e['orders']) == 2
        assert all(r['reporter_user_id']==ctx['identities']['resident'] and r['tenant_id']==ctx['tenant_id'] and r['property_id']==ctx['property_id'] for r in e['orders'])
        e['business_counts']={table:c.execute(text(f'SELECT COUNT(*) FROM {table} WHERE work_order_id IN (:history,:active)'),params).scalar_one() for table in ('work_order_assignments','work_order_processes','work_order_receipts','work_order_logs','work_order_feedbacks')}
    worker='auth001@DESKTOP-8NHSE12'
    inspection=celery_app.control.inspect(destination=[worker],timeout=8)
    e['idle']={}
    for name in ('active','reserved','scheduled'):
        value=getattr(inspection,name)()
        assert value is not None and worker in value
        e['idle'][name]=len(value[worker])
        assert e['idle'][name]==0
    result=subprocess.run(['docker','exec','property-ai-rabbitmq-1','rabbitmqctl','list_queues','-p','property_ai_auth001','name','messages_ready','messages_unacknowledged','consumers'],capture_output=True,text=True,timeout=30)
    assert result.returncode==0
    row=next(x.split() for x in result.stdout.splitlines() if x.startswith('property_ai.auth001\t'))
    assert row[1:]==['0','0','1']
    e['queue']={'vhost':'property_ai_auth001','actual_output':result.stdout,'shared_access':False,'message_content_read':False}
    e['observed_at_utc']=datetime.now(UTC).isoformat()
    (OUT/'preflight.json').write_text(json.dumps(e,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    print(json.dumps(e,ensure_ascii=False,default=str))
finally:
    engine.dispose()
