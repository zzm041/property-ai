"""SELECT-only current-run persistence; inspect counters, never consume/replay messages."""
from pathlib import Path
from datetime import UTC, datetime
import hashlib
import json
import runpy
import subprocess

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).parent
h = runpy.run_path(str(ROOT / 'reports/notification-events-inbox-013/run-runtime.py'))
from sqlalchemy import create_engine, text
from auth001_isolated_runtime import apply_isolated_environ
from app.worker.failed_tasks import get_failed_task

settings = h['_guard']()
target = OUT / 'worker-database-evidence.json'
assert not target.exists(), 'Preserve evidence checkpoint'
ctx = json.loads((ROOT / 'tmp/ai-fallback-runtime-022/context.json').read_text(encoding='utf-8'))
before = json.loads((OUT / 'preflight.json').read_text(encoding='utf-8'))
page_path = OUT / 'page-evidence.json'
page = json.loads(page_path.read_text(encoding='utf-8'))
posts = [r for r in page['requests'] if r['method'] == 'POST']
assert len(posts) == 1 and posts[0]['path'] == '/work-orders' and posts[0]['status'] == 200
assert posts[0]['result']['id'] == ctx['work_order_id']
assert all(r['status'] == 200 for r in page['requests'])
for key in ('01-created-despite-provider-failure', '02-resident-persisted-report', '04-relogin-same-pending-report'):
    w = page['steps'][key]['workOrder']
    assert w['id'] == ctx['work_order_id'] and w['status'] == 'PENDING_ANALYSIS' and w['current_assignment_id'] is None
assert page['steps']['03-manager-fallback-queue']['selectedId'] == ctx['work_order_id']
events = []
for line in (ROOT / 'tmp/auth001-runtime/worker-d5c9098-fallback022-unavailable.stdout.log').read_text(encoding='utf-8').splitlines():
    try:
        row = json.loads(line)
    except ValueError:
        continue
    if row.get('request_id') == ctx['create_request_id'] and row.get('event') in ('ai_analysis_retry', 'failed_task_recorded', 'celery_task_dead_lettered'):
        events.append({k: row[k] for k in ('timestamp', 'event', 'task_id', 'request_id', 'error_code', 'redis_ok', 'dlq_ok', 'dlq') if k in row})
task_ids = {r['task_id'] for r in events}
assert len(task_ids) == 1
task_id = task_ids.pop()
assert sum(r['event'] == 'ai_analysis_retry' for r in events) == 3
assert any(r['event'] == 'failed_task_recorded' and r['redis_ok'] and r['dlq_ok'] for r in events)
e = {'task_id': 'AI-FALLBACK-RUNTIME-022', 'run_id': ctx['run_id'], 'business_code_sha': page['code_sha'],
     'page_source_sha256': hashlib.sha256(page_path.read_bytes()).hexdigest(), 'actual_create_status': 200,
     'helper_correction': 'Original helper incorrectly expected201. API router default and actual wx.request are200; all page steps completed. Original error preserved; no page replay.',
     'worker_events': events, 'celery_task_id': task_id,
     'database': '127.0.0.1:3307/property_ai_auth001_test', 'redis_database': 12}
engine = create_engine(settings.test_database_url)
try:
    with engine.connect() as c:
        c.execute(text('SET TRANSACTION READ ONLY'))
        e['revision'] = c.execute(text('SELECT version_num FROM alembic_version')).scalar_one()
        assert e['revision'] == '20260919_0009'
        params = {'id': ctx['work_order_id'], 'tenant': ctx['tenant_id'], 'property': ctx['property_id']}
        e['work_order'] = dict(c.execute(text('SELECT id,tenant_id,property_id,house_id,reporter_user_id,status,version,current_assignment_id FROM work_orders WHERE id=:id AND tenant_id=:tenant AND property_id=:property'), params).mappings().one())
        assert e['work_order']['status'] == 'PENDING_ANALYSIS' and e['work_order']['version'] == 1 and e['work_order']['current_assignment_id'] is None
        e['analyses'] = [dict(r) for r in c.execute(text('SELECT id,work_order_id,analysis_version,status,error_code,input_hash,prompt_version FROM work_order_analyses WHERE work_order_id=:id ORDER BY id'), params).mappings()]
        e['ai_calls'] = [dict(r) for r in c.execute(text('SELECT id,work_order_id,request_id,provider_type,provider_mode,status,attempt,error_code,input_hash,prompt_version,started_at,finished_at FROM ai_call_logs WHERE work_order_id=:id ORDER BY id'), params).mappings()]
        # Existing FAILED analysis is reused across retry attempts, not re-versioned.
        assert len(e['analyses']) == 1
        assert len(e['ai_calls']) == 3
        assert all(r['status'] == 'FAILED' and r['error_code'] == 'AI.UNAVAILABLE' for r in e['analyses'] + e['ai_calls'])
        assert [r['attempt'] for r in e['ai_calls']] == [1, 2, 3]
        assert all(r['request_id'] == ctx['create_request_id'] and r['provider_mode'] == 'DEVELOPMENT' for r in e['ai_calls'])
        e['assignment_count'] = c.execute(text('SELECT COUNT(*) FROM work_order_assignments WHERE work_order_id=:id'), params).scalar_one()
        assert e['assignment_count'] == 0
        e['history'] = [dict(r) for r in c.execute(text('SELECT id,actor_user_id,event_code,from_status,to_status,created_at FROM work_order_logs WHERE work_order_id=:id ORDER BY id'), params).mappings()]
        assert len(e['history']) == 1 and e['history'][0]['event_code'] == 'CREATE'
        e['audit'] = [dict(r) for r in c.execute(text("SELECT id,actor_user_id,action,resource_type,resource_id,request_id,details_json,created_at FROM audit_logs WHERE resource_type='WORK_ORDER_ANALYSIS' AND resource_id IN (SELECT id FROM work_order_analyses WHERE work_order_id=:id) ORDER BY id"), params).mappings()]
        assert sum(r['action'] == 'WORK_ORDER_AI_ANALYSIS_FAILED' for r in e['audit']) == 3
        e['loads'] = [dict(r) for r in c.execute(text('SELECT employee_id,property_id,business_date,on_duty,current_load FROM employee_daily_statuses WHERE property_id=:property ORDER BY employee_id,business_date'), params).mappings()]
        assert json.loads(json.dumps(e['loads'], default=str)) == before['loads']
        prior = [dict(r) for r in c.execute(text('SELECT id,status,version FROM work_orders WHERE tenant_id=:tenant AND property_id=:property AND id<>:id ORDER BY id'), params).mappings()]
        assert prior == before['existing_orders']
        e['prior_orders_unchanged'] = prior
    e['failed_task'] = get_failed_task(task_id, settings)
    assert e['failed_task'] and e['failed_task']['args'] == [ctx['work_order_id'], ctx['create_request_id']]
    assert e['failed_task']['error_code'] == 'AI.UNAVAILABLE' and not e['failed_task']['replayed']
    apply_isolated_environ(settings)
    from app.worker.celery_app import celery_app
    worker = 'auth001@DESKTOP-8NHSE12'
    inspector = celery_app.control.inspect(destination=[worker], timeout=8)
    e['idle'] = {}
    for name in ('active', 'reserved', 'scheduled'):
        result = getattr(inspector, name)()
        assert result is not None and worker in result
        e['idle'][name] = len(result[worker])
        assert e['idle'][name] == 0
    queue = subprocess.run(['docker', 'exec', 'property-ai-rabbitmq-1', 'rabbitmqctl', 'list_queues', '-p', 'property_ai_auth001', 'name', 'messages_ready', 'messages_unacknowledged', 'consumers'], capture_output=True, text=True, timeout=30)
    assert queue.returncode == 0
    rows = [s.split() for s in queue.stdout.splitlines()]
    assert next(r for r in rows if r[0] == 'property_ai.auth001')[1:] == ['0', '0', '1']
    assert next(r for r in rows if r[0] == 'property_ai.auth001.dlq')[1:] == ['3', '0', '0']
    e['queue'] = {'vhost': 'property_ai_auth001', 'actual_output': queue.stdout, 'historical_dlq_before': 2, 'new_failed_task_delta': 1, 'shared_access': False, 'message_content_read': False, 'replay_or_purge': False}
    e['status'] = 'BUSINESS_FALLBACK_CONFIRMED_LOGGING_FIX_PENDING'
    e['observed_at_utc'] = datetime.now(UTC).isoformat()
    target.write_text(json.dumps(e, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    print(json.dumps({'run_id': ctx['run_id'], 'order': ctx['work_order_id'], 'attempts': len(e['ai_calls']), 'assignments': e['assignment_count'], 'idle': e['idle'], 'status': e['status']}))
finally:
    engine.dispose()
