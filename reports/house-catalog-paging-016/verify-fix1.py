"""B1-only mixed-status fixture and evidence; normal API writes, SELECT-only DB."""
from pathlib import Path
from datetime import UTC, datetime
import json
import runpy
import subprocess

REPO = Path(__file__).resolve().parents[2]
OUT = Path(__file__).parent
PRIVATE = REPO / 'tmp/house-catalog-paging-016/context.json'
helpers = runpy.run_path(str(REPO / 'reports/notification-events-inbox-013/run-runtime.py'))
from sqlalchemy import create_engine, text


def main():
    settings = helpers['_guard']()
    ctx = json.loads(PRIVATE.read_text(encoding='utf-8'))
    chosen = ctx['houses'][0]
    result = {'task_id': 'HOUSE-CATALOG-PAGING-016', 'finding': 'HOUSE016-B1',
              'repair_round': 1, 'run_id': ctx['run_id'],
              'code_sha': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=REPO, text=True).strip(),
              'mode': 'Normal manager API sets one existing016 synthetic house INACTIVE; catalog GETs and read-only SQL only. No replay of015 or initial016 flows.'}
    engine = create_engine(settings.test_database_url)
    client = helpers['Client'](ctx['run_id'] + '-fix1')
    try:
        with engine.connect() as conn:
            conn.execute(text('SET TRANSACTION READ ONLY'))
            result['revision'] = conn.execute(text('SELECT version_num FROM alembic_version')).scalar_one()
            assert result['revision'] == '20260919_0009'
            record = dict(conn.execute(text('SELECT id,tenant_id,property_id,unit_id,room_no,status FROM houses WHERE id=:id'), {'id': chosen}).mappings().one())
            assert record['room_no'] == f'016-{ctx["run_id"][-6:]}-00'
            assert all(record[k] == ctx[k] for k in ('tenant_id', 'property_id', 'unit_id'))
            count = conn.execute(text('SELECT count(*) FROM work_orders WHERE house_id=:id'), {'id': chosen}).scalar_one()
            assert count == 0
            result['precondition'] = {'house': record, 'work_order_count': count}
        fix = ctx.setdefault('fix1', {})
        for actor in ('manager', 'resident'):
            # Fresh ordinary SMS authentication, never Seed login or direct credential edits.
            token = client.sms_login(ctx['phones'][actor], 'login-' + actor)
            fix['login-' + actor] = token
            me = client.data('GET', '/me', token=token['access_token'], label='verify-' + actor)
            assert me['user_id'] == ctx['identities'][actor] and 'SYSTEM_ADMIN' not in me['roles']
            PRIVATE.write_text(json.dumps(ctx, ensure_ascii=False, indent=2), encoding='utf-8')
        manager = fix['login-manager']['access_token']
        resident = fix['login-resident']['access_token']
        if record['status'] == 'ACTIVE':
            fix['inactive_house'] = client.data('PATCH', f'/admin/houses/{chosen}', token=manager,
                                                body={'status': 'INACTIVE'}, label='set-tagged-house-inactive')
            PRIVATE.write_text(json.dumps(ctx, ensure_ascii=False, indent=2), encoding='utf-8')
        else:
            assert record['status'] == 'INACTIVE'
            result['resumed_without_repeating_mutation'] = True
        catalog = f'/units/{ctx["unit_id"]}/houses?limit=50'
        first = client.data('GET', catalog, token=resident, label='active-first')['items']
        assert len(first) == 50 and chosen not in [r['id'] for r in first]
        second = client.data('GET', catalog + f'&after_id={first[-1]["id"]}', token=resident, label='active-second')['items']
        assert len(second) == 2 and ctx['target_house_id'] in [r['id'] for r in second]
        assert not {r['id'] for r in first}.intersection(r['id'] for r in second)
        assert min(r['id'] for r in second) > max(r['id'] for r in first)
        assert not client.data('GET', catalog + f'&after_id={second[-1]["id"]}', token=resident, label='active-final')['items']
        archive = client.data('GET', f'/admin/property-archive?tenant_id={ctx["tenant_id"]}&property_id={ctx["property_id"]}', token=manager, label='archive-retains-inactive')
        assert next(r for r in archive['houses'] if r['id'] == chosen)['status'] == 'INACTIVE'
        result['api'] = {'first': first, 'second': second, 'empty_terminal_page': True,
                         'archive_retains_inactive_house': chosen}
        with engine.connect() as conn:
            conn.execute(text('SET TRANSACTION READ ONLY'))
            rows = [dict(r) for r in conn.execute(text('SELECT id,room_no,status FROM houses WHERE tenant_id=:tenant_id AND property_id=:property_id AND unit_id=:unit_id ORDER BY id'), ctx).mappings()]
            assert len(rows) == 53
            assert [r['id'] for r in rows if r['status'] == 'INACTIVE'] == [chosen]
            assert chosen in [r['id'] for r in rows[:50]] and rows[-1]['status'] == 'ACTIVE'
            assert [r['id'] for r in rows if r['status'] == 'ACTIVE'] == [r['id'] for r in first + second]
            result['database'] = {'target': '127.0.0.1:3307/property_ai_auth001_test', 'houses_in_physical_id_order': rows,
                                  'first_physical_page_contains_inactive': True, 'active_after_physical_page': True,
                                  'active_api_order_matches_database': True}
        queue = subprocess.run(['docker', 'exec', 'property-ai-rabbitmq-1', 'rabbitmqctl', 'list_queues', '-p',
                                'property_ai_auth001', 'name', 'messages_ready', 'messages_unacknowledged', 'consumers'],
                               capture_output=True, text=True, timeout=30)
        assert queue.returncode == 0
        result['queue'] = {'vhost': 'property_ai_auth001', 'actual_output': queue.stdout, 'message_content_read': False,
                           'shared_vhost_accessed': False}
        result['requests'] = client.requests
        result['collected_at_utc'] = datetime.now(UTC).isoformat()
        result['status'] = 'FIX1_API_DB_PASS_PENDING_REVIEW'
        (OUT / 'fix1-api-database-queue-evidence.json').write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
        print(json.dumps({'run_id': ctx['run_id'], 'inactive_house': chosen, 'active_pages': [len(first), len(second)], 'status': result['status']}))
    finally:
        client.close()
        engine.dispose()


if __name__ == '__main__':
    main()
