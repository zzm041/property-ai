"""Current synthetic identity/house and exact unique-worker idle preflight; SELECT only."""
from pathlib import Path
from datetime import UTC, datetime
import json
import runpy
import secrets
import subprocess

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).parent
PRIVATE = ROOT / 'tmp/ai-fallback-runtime-022/context.json'
h = runpy.run_path(str(ROOT / 'reports/notification-events-inbox-013/run-runtime.py'))
from sqlalchemy import create_engine, text
from auth001_isolated_runtime import apply_isolated_environ

assert not (OUT / 'preflight.json').exists(), 'Preserve checkpoint'
settings = h['_guard']()
assert settings.llm_provider_behavior == 'success'
prior = json.loads((ROOT / 'tmp/notification-presentation-021/context.json').read_text(encoding='utf-8'))
ctx = {k: prior[k] for k in ('phones', 'identities', 'tenant_id', 'property_id')}
ctx.update(run_id='fallback022-' + secrets.token_hex(6), last_sms_sent_at={})
PRIVATE.parent.mkdir(parents=True, exist_ok=True)
engine = create_engine(settings.test_database_url)
try:
    with engine.connect() as c:
        c.execute(text('SET TRANSACTION READ ONLY'))
        revision = c.execute(text('SELECT version_num FROM alembic_version')).scalar_one()
        assert revision == '20260919_0009'
        # A currently ACTIVE relation is queried, not a presumed house number.
        relations = [dict(r) for r in c.execute(text('SELECT id,user_id,tenant_id,property_id,house_id,status FROM resident_house_relations WHERE user_id=:user AND tenant_id=:tenant AND property_id=:property AND status=:status AND valid_from<=UTC_TIMESTAMP(6) AND (valid_to IS NULL OR valid_to>UTC_TIMESTAMP(6)) ORDER BY id'), {'user':ctx['identities']['resident'], 'tenant':ctx['tenant_id'], 'property':ctx['property_id'], 'status':'ACTIVE'}).mappings()]
        assert relations
        ctx['house_id'] = relations[0]['house_id']
        users = [dict(r) for r in c.execute(text('SELECT id,status,display_name FROM users WHERE id IN (:resident,:manager)'), {'resident':ctx['identities']['resident'], 'manager':ctx['identities']['manager']}).mappings()]
        assert len(users) == 2 and all(r['status'] == 'ACTIVE' for r in users)
        loads = [dict(r) for r in c.execute(text('SELECT employee_id,property_id,business_date,on_duty,current_load FROM employee_daily_statuses WHERE property_id=:property ORDER BY employee_id,business_date'), {'property':ctx['property_id']}).mappings()]
        orders = [dict(r) for r in c.execute(text('SELECT id,status,version FROM work_orders WHERE tenant_id=:tenant AND property_id=:property ORDER BY id'), {'tenant':ctx['tenant_id'], 'property':ctx['property_id']}).mappings()]
    apply_isolated_environ(settings)
    from app.worker.celery_app import celery_app
    worker = 'auth001@DESKTOP-8NHSE12'
    inspector = celery_app.control.inspect(destination=[worker], timeout=8)
    idle = {}
    for name in ('active', 'reserved', 'scheduled'):
        value = getattr(inspector, name)()
        assert value is not None and worker in value
        idle[name] = len(value[worker])
        assert idle[name] == 0
    q = subprocess.run(['docker','exec','property-ai-rabbitmq-1','rabbitmqctl','list_queues','-p','property_ai_auth001','name','messages_ready','messages_unacknowledged','consumers'],capture_output=True,text=True,timeout=30)
    assert q.returncode == 0
    row = next(x.split() for x in q.stdout.splitlines() if x.startswith('property_ai.auth001\t'))
    assert row[1:] == ['0','0','1']
    PRIVATE.write_text(json.dumps(ctx,ensure_ascii=False,indent=2),encoding='utf-8')
    e = {'task_id':'AI-FALLBACK-RUNTIME-022','run_id':ctx['run_id'],'base_sha':'d5c9098c59b1d67af34b59b859006bf1369eb0ed','revision':revision,'database':'127.0.0.1:3307/property_ai_auth001_test','users':users,'relations':relations,'loads':loads,'existing_orders':orders,'idle':idle,'queue':{'vhost':'property_ai_auth001','actual_output':q.stdout,'shared_access':False,'message_content_read':False},'observed_at_utc':datetime.now(UTC).isoformat()}
    (OUT / 'preflight.json').write_text(json.dumps(e,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    print(json.dumps({'run_id':ctx['run_id'],'house_id':ctx['house_id'],'existing_orders':len(orders),'idle':idle}))
finally:
    engine.dispose()
