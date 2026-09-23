"""Select existing current-run notifications only; no business-data writes."""
from pathlib import Path
from datetime import UTC, datetime
import json
import runpy
import secrets
import subprocess

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).parent
PRIVATE = ROOT / 'tmp/notification-presentation-021/context.json'
h = runpy.run_path(str(ROOT / 'reports/notification-events-inbox-013/run-runtime.py'))
from sqlalchemy import create_engine, text

assert not (OUT / 'preflight.json').exists(), 'Preserve existing checkpoint'
settings = h['_guard']()
prior = json.loads((ROOT / 'tmp/resident-wo-presentation-020/context.json').read_text(encoding='utf-8'))
ctx = {k: prior[k] for k in ('phones', 'identities', 'tenant_id', 'property_id', 'history_order_id')}
ctx.update(run_id='ntfview021-' + secrets.token_hex(6), last_sms_sent_at={})
PRIVATE.parent.mkdir(parents=True, exist_ok=True)
engine = create_engine(settings.test_database_url)
try:
    with engine.connect() as c:
        c.execute(text('SET TRANSACTION READ ONLY'))
        revision = c.execute(text('SELECT version_num FROM alembic_version')).scalar_one()
        assert revision == '20260919_0009'
        rows = [dict(r) for r in c.execute(text('SELECT id,tenant_id,property_id,user_id,title,body,resource_type,resource_id,read_at,created_at FROM notifications WHERE user_id=:user ORDER BY id DESC LIMIT 200'), {'user': ctx['identities']['resident']}).mappings()]
        candidates = [r for r in rows if r['resource_type'] == 'WORK_ORDER' and r['resource_id'] == ctx['history_order_id'] and r['title'] == '工单待确认' and r['body'] == 'Please confirm the work order result']
        assert candidates, 'No existing target; do not recreate workflow'
        target = next((r for r in candidates if r['read_at'] is None), candidates[0])
        ctx['notification_id'] = target['id']
        order = dict(c.execute(text('SELECT id,status,version FROM work_orders WHERE id=:id AND reporter_user_id=:user AND tenant_id=:tenant AND property_id=:property'), {'id': ctx['history_order_id'], 'user': ctx['identities']['resident'], 'tenant': ctx['tenant_id'], 'property': ctx['property_id']}).mappings().one())
        counts = {table: c.execute(text(f'SELECT COUNT(*) FROM {table} WHERE work_order_id=:id'), {'id': order['id']}).scalar_one() for table in ('work_order_assignments', 'work_order_processes', 'work_order_receipts', 'work_order_logs', 'work_order_feedbacks')}
    PRIVATE.write_text(json.dumps(ctx, ensure_ascii=False, indent=2), encoding='utf-8')
    queue = subprocess.run(['docker', 'exec', 'property-ai-rabbitmq-1', 'rabbitmqctl', 'list_queues', '-p', 'property_ai_auth001', 'name', 'messages_ready', 'messages_unacknowledged', 'consumers'], capture_output=True, text=True, timeout=30)
    assert queue.returncode == 0
    e = {'task_id': 'NOTIFICATION-PRESENTATION-021', 'run_id': ctx['run_id'], 'product_sha': '3c85cd4b9e0d9045a0ffa25b8894f83a8ad5e929', 'backend_unchanged_since': 'b75fa97112b3144f9d4702e4782b231bb775c19c', 'revision': revision, 'database': '127.0.0.1:3307/property_ai_auth001_test', 'target': target, 'notifications': rows, 'work_order': order, 'lifecycle_counts': counts, 'queue': {'vhost': 'property_ai_auth001', 'actual_output': queue.stdout, 'message_content_read': False, 'shared_access': False}, 'observed_at_utc': datetime.now(UTC).isoformat()}
    (OUT / 'preflight.json').write_text(json.dumps(e, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    print(json.dumps({'run_id': ctx['run_id'], 'notification_count': len(rows), 'target': target['id'], 'order': order, 'target_initially_read': target['read_at'] is not None}))
finally:
    engine.dispose()
