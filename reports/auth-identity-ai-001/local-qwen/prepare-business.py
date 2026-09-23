"""Read-only fresh AUTH001 preflight and private synthetic-account checkpoint."""
from pathlib import Path
from datetime import UTC, datetime
import json
import runpy
import secrets
import subprocess

ROOT = Path(r"D:\property-ai")
OUT = Path(__file__).parent
PRIVATE = ROOT / 'tmp/selfhost-ai-local/business-context.json'
h = runpy.run_path(str(ROOT / 'reports/notification-events-inbox-013/run-runtime.py'))
from sqlalchemy import create_engine, text
from auth001_isolated_runtime import apply_isolated_environ

assert not (OUT / 'business-preflight.json').exists(), 'Inspect existing checkpoint, do not overwrite'
assert not PRIVATE.exists(), 'Private checkpoint exists'
settings = h['_guard']()
prior = json.loads((ROOT / 'tmp/ai-fallback-runtime-022/context.json').read_text(encoding='utf-8'))
ctx = {key: prior[key] for key in ('phones', 'identities', 'tenant_id', 'property_id')}
ctx.update(run_id='localqwen-' + secrets.token_hex(6), last_sms_sent_at={})
engine = create_engine(settings.test_database_url)
try:
    with engine.connect() as connection:
        connection.execute(text('SET TRANSACTION READ ONLY'))
        revision = connection.execute(text('SELECT version_num FROM alembic_version')).scalar_one()
        assert revision == '20260919_0009'
        relations = [dict(row) for row in connection.execute(text('SELECT r.id,r.user_id,r.house_id,r.tenant_id,r.property_id,r.relation_type,p.name AS property_name,b.name AS building_name,u.name AS unit_name,h.room_no FROM resident_house_relations r JOIN houses h ON h.id=r.house_id AND h.tenant_id=r.tenant_id AND h.property_id=r.property_id JOIN units u ON u.id=h.unit_id AND u.tenant_id=h.tenant_id AND u.property_id=h.property_id JOIN buildings b ON b.id=u.building_id AND b.tenant_id=h.tenant_id AND b.property_id=h.property_id JOIN properties p ON p.id=h.property_id AND p.tenant_id=h.tenant_id WHERE r.user_id=:user AND r.status="ACTIVE" AND r.valid_from<=UTC_TIMESTAMP(6) AND (r.valid_to IS NULL OR r.valid_to>UTC_TIMESTAMP(6)) ORDER BY r.id'), {'user':ctx['identities']['resident']}).mappings()]
        assert relations
        ctx['house_id'] = relations[0]['house_id']
        loads = [dict(row) for row in connection.execute(text('SELECT employee_id,property_id,business_date,on_duty,current_load FROM employee_daily_statuses WHERE property_id=:property ORDER BY employee_id,business_date'), {'property':ctx['property_id']}).mappings()]
        prior_orders = [dict(row) for row in connection.execute(text('SELECT id,status,version FROM work_orders WHERE tenant_id=:tenant AND property_id=:property ORDER BY id'), {'tenant':ctx['tenant_id'],'property':ctx['property_id']}).mappings()]
    apply_isolated_environ(settings)
    from app.worker.celery_app import celery_app
    worker = 'auth001@DESKTOP-8NHSE12'
    inspector = celery_app.control.inspect(destination=[worker], timeout=8)
    idle = {}
    for name in ('active','reserved','scheduled'):
        rows = getattr(inspector, name)()
        assert rows is not None and worker in rows
        idle[name] = len(rows[worker])
        assert idle[name] == 0
    queue = subprocess.run(['docker','exec','property-ai-rabbitmq-1','rabbitmqctl','list_queues','-p','property_ai_auth001','name','messages_ready','messages_unacknowledged','consumers'],capture_output=True,text=True,timeout=30,check=True)
    main_queue = next(row.split() for row in queue.stdout.splitlines() if row.startswith('property_ai.auth001\t'))
    assert main_queue[1:] == ['0','0','1']
    code_sha = subprocess.check_output(['git','-C',str(ROOT),'rev-parse','HEAD'],text=True).strip()
    ctx['code_sha'] = code_sha
    PRIVATE.write_text(json.dumps(ctx,ensure_ascii=False,indent=2),encoding='utf-8')
    public = {'task_id':'SELFHOST-AI-LOCAL-001','run_id':ctx['run_id'],'code_sha':code_sha,'at_utc':datetime.now(UTC).isoformat(),
              'database':'127.0.0.1:3307/property_ai_auth001_test','revision':revision,'relations':relations,'staff_daily':loads,
              'prior_orders':prior_orders,'worker_idle':idle,'vhost':'property_ai_auth001','queue_output':queue.stdout,
              'queue_content_read':False,'shared_vhost_access':False,'fixture_mutations':False}
    (OUT/'business-preflight.json').write_text(json.dumps(public,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    print(json.dumps({'run_id':ctx['run_id'],'house_id':ctx['house_id'],'relation_count':len(relations),'idle':idle,'staff_daily':loads},default=str))
finally:
    engine.dispose()
