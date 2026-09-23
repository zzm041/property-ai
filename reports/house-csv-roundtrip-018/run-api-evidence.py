"""018 API negatives and SELECT-only same-object persistence, separate from actual UI."""
from pathlib import Path
from datetime import UTC, datetime
import json
import runpy
import subprocess

REPO = Path(__file__).resolve().parents[2]
OUT = Path(__file__).parent
PRIVATE = REPO / 'tmp/house-csv-roundtrip-018/context.json'
h = runpy.run_path(str(REPO / 'reports/notification-events-inbox-013/run-runtime.py'))
from sqlalchemy import create_engine, text


def main():
    settings = h['_guard']()
    ctx = json.loads(PRIVATE.read_text(encoding='utf-8'))
    page = json.loads((OUT / 'page-evidence.json').read_text(encoding='utf-8'))
    pre = json.loads((OUT / 'preflight.json').read_text(encoding='utf-8'))
    assert page['status'] == 'PAGE_PASS_PENDING_INDEPENDENT_REVIEW'
    path = OUT / 'api-database-evidence.json'
    state = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {
        'task_id': 'HOUSE-CSV-ROUNDTRIP-018', 'run_id': ctx['run_id'], 'frontend_sha': page['code_sha'],
        'backend_sha': pre['backend_sha'], 'results': {}, 'requests': [],
        'mode': 'Normal existing manager page session and fresh other-resident SMS session. No Seed login; API negatives are not UI. SQL SELECT-only.',
    }
    assert not state.get('status'), 'Completed, no replay'
    client = h['Client'](ctx['run_id'])
    engine = create_engine(settings.test_database_url)

    def save():
        state['requests'].extend(client.requests)
        client.requests.clear()
        path.write_text(json.dumps(state, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
        PRIVATE.write_text(json.dumps(ctx, ensure_ascii=False, indent=2), encoding='utf-8')

    def step(label, fn):
        if label not in state['results']:
            state['results'][label] = fn()
            save()
        return state['results'][label]

    try:
        manager = ctx['manager_token']
        me = client.data('GET', '/me', token=manager, label='manager-current')
        assert me['user_id'] == ctx['identities']['manager'] and 'SYSTEM_ADMIN' not in me['roles']
        if 'other-auth' not in ctx['done']:
            ctx['done']['other-auth'] = client.sms_login(ctx['phones']['other'], 'other-auth')
            save()
        other = ctx['done']['other-auth']['access_token']
        scope = {'property_id': ctx['property_id'], 'tenant_id': ctx['tenant_id']}
        exported = step('current-export', lambda: client.data('GET', '/admin/houses/export', params=scope, token=manager, label='current-export'))
        marked = [r for r in exported['rows'] if r['building_code'] == ctx['building_code']]
        assert len(marked) == 2 and len(exported['rows']) == pre['house_count_before'] + 2
        body = {**scope, 'rows': marked}
        step('resident-import-denied', lambda: client.call('POST', '/admin/houses/import', body=body, token=other, label='resident-import-denied', expected=(403,)))
        step('resident-export-denied', lambda: client.call('GET', '/admin/houses/export', params=scope, token=other, label='resident-export-denied', expected=(403,)))
        step('over500-rejected', lambda: client.call('POST', '/admin/houses/import', body={**scope, 'rows': [marked[0]] * 501}, token=manager, label='over500-rejected', expected=(422,)))
        conflict = [{**r, 'building_name': r['building_name'] + '-conflict'} for r in marked]
        step('existing-conflict', lambda: client.call('POST', '/admin/houses/import', body={**scope, 'rows': conflict}, token=manager, label='existing-conflict', expected=(409,)))
        with engine.connect() as c:
            c.execute(text('SET TRANSACTION READ ONLY'))
            revision = c.execute(text('SELECT version_num FROM alembic_version')).scalar_one()
            assert revision == '20260919_0009'
            records = [dict(r) for r in c.execute(text('SELECT b.id AS building_id,b.code AS building_code,b.name AS building_name,u.id AS unit_id,u.name AS unit_name,h.id AS house_id,h.room_no,h.floor,h.status,h.tenant_id,h.property_id FROM houses h JOIN units u ON h.unit_id=u.id JOIN buildings b ON u.building_id=b.id WHERE b.code=:code AND h.property_id=:property ORDER BY h.id'), {'code':ctx['building_code'],'property':ctx['property_id']}).mappings()]
            assert [r['house_id'] for r in records] == ctx['house_ids']
            assert len({r['building_id'] for r in records}) == 1 and len({r['unit_id'] for r in records}) == 1
            assert all(r['building_name'] == marked[0]['building_name'] and r['unit_name'] == marked[0]['unit_name'] for r in records)
            first = next(r for r in page['requests'] if r['path'] == '/admin/houses/import' and r['result']['created_houses'] == 2)
            audits = [dict(r) for r in c.execute(text('SELECT id,actor_user_id,action,resource_type,resource_id,request_id FROM audit_logs WHERE request_id=:request ORDER BY id'), {'request':first['request_id']}).mappings()]
            assert len(audits) == 4 and all(r['actor_user_id'] == ctx['identities']['manager'] for r in audits)
            total = c.execute(text('SELECT count(*) FROM houses WHERE property_id=:id'), {'id':ctx['property_id']}).scalar_one()
            assert total == pre['house_count_before'] + 2
            state['database'] = {'target': '127.0.0.1:3307/property_ai_auth001_test', 'revision': revision, 'records': records,
                                 'first_import_audits': audits, 'total_houses': total, 'old_count_before': pre['house_count_before']}
        q = subprocess.run(['docker','exec','property-ai-rabbitmq-1','rabbitmqctl','list_queues','-p','property_ai_auth001','name','messages_ready','messages_unacknowledged','consumers'],capture_output=True,text=True,timeout=30)
        assert q.returncode == 0
        state['queue'] = {'vhost': 'property_ai_auth001', 'actual_output': q.stdout, 'message_content_read': False, 'shared_vhost_accessed': False}
        state['observed_at_utc'] = datetime.now(UTC).isoformat()
        state['status'] = 'API_DB_PASS_PENDING_INDEPENDENT_REVIEW'
        save()
        print(json.dumps({'run_id':ctx['run_id'], 'house_ids':ctx['house_ids'], 'status':state['status']}))
    finally:
        save()
        client.close()
        engine.dispose()


if __name__ == '__main__':
    main()
