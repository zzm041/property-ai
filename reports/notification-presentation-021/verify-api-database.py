"""Verify the completed page against normal API reads and SELECT-only persistence."""
from pathlib import Path
from datetime import UTC, datetime
import json
import runpy
import subprocess

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).parent
h = runpy.run_path(str(ROOT / 'reports/notification-events-inbox-013/run-runtime.py'))
from sqlalchemy import create_engine, text


def instant(value):
    return datetime.fromisoformat(str(value).replace('Z', '+00:00')).replace(tzinfo=UTC)


def main():
    settings = h['_guard']()
    before = json.loads((OUT / 'preflight.json').read_text(encoding='utf-8'))
    page = json.loads((OUT / 'page-evidence.json').read_text(encoding='utf-8'))
    assert page['complete'] and page['code_sha'] == before['product_sha']
    ctx = json.loads((ROOT / 'tmp/notification-presentation-021/context.json').read_text(encoding='utf-8'))
    target = OUT / 'api-database-evidence.json'
    assert not target.exists(), 'Preserve checkpoint; no replay'
    client = h['Client'](ctx['run_id'])
    engine = create_engine(settings.test_database_url)
    e = {'task_id': before['task_id'], 'run_id': ctx['run_id'], 'product_sha': page['code_sha'],
         'backend_unchanged_since': before['backend_unchanged_since'], 'requests': [], 'api_pages': []}
    try:
        source_delta = subprocess.run(['git', 'diff', '--name-only', before['backend_unchanged_since'], page['code_sha'], '--', 'backend/app', 'backend/alembic', 'backend/pyproject.toml'], capture_output=True, text=True, check=True)
        assert not source_delta.stdout.strip(), 'Unexpected backend change'
        e['backend_source_delta'] = source_delta.stdout
        e['health'] = client.data('GET', '/healthz', label='api-health')
        me = client.data('GET', '/me', token=ctx['resident_token'], label='normal-principal')
        assert me['user_id'] == ctx['identities']['resident'] and 'SYSTEM_ADMIN' not in me['roles']
        e['principal'] = {'user_id': me['user_id'], 'roles': me['roles']}
        cursor = 0
        items = []
        for index in range(10):
            value = client.data('GET', '/notifications', token=ctx['resident_token'], params={'after_id': cursor, 'limit': 20}, label=f'inbox-{index}')
            e['api_pages'].append(value)
            items.extend(value['items'])
            if value['next_cursor'] is None:
                break
            assert value['next_cursor'] < cursor if cursor else value['next_cursor'] > 0
            cursor = value['next_cursor']
        else:
            raise AssertionError('More than10pages; bounded verification')
        assert [r['id'] for r in items] == [r['id'] for r in before['notifications']], 'No event creation authorized'
        after_view = page['steps']['05-relogin-same-notification']
        assert [r['id'] for r in after_view['items']] == [r['id'] for r in items]
        assert after_view['unread'] == e['api_pages'][-1]['unread_count']
        with engine.connect() as c:
            c.execute(text('SET TRANSACTION READ ONLY'))
            e['revision'] = c.execute(text('SELECT version_num FROM alembic_version')).scalar_one()
            assert e['revision'] == '20260919_0009'
            db = [dict(r) for r in c.execute(text('SELECT id,tenant_id,property_id,user_id,title,body,resource_type,resource_id,read_at,created_at FROM notifications WHERE user_id=:user ORDER BY id DESC LIMIT 200'), {'user': me['user_id']}).mappings()]
            assert len(db) == len(items) == len(before['notifications'])
            for api, row, prior, view in zip(items, db, before['notifications'], after_view['items']):
                for key in ('id', 'tenant_id', 'property_id', 'title', 'body', 'resource_type', 'resource_id'):
                    assert api[key] == row[key] == prior[key] == view[key]
                assert instant(api['created_at']) == row['created_at'].replace(tzinfo=UTC) == instant(prior['created_at'])
                if row['id'] != ctx['notification_id']:
                    assert str(row['read_at']) == str(prior['read_at'])
                else:
                    assert row['read_at'] is not None and instant(api['read_at']) == row['read_at'].replace(tzinfo=UTC) == instant(ctx['target_read_at'])
                    assert view['read_at'] == api['read_at']
                    assert view['readAtLabel'] == row['read_at'].strftime('%Y-%m-%d %H:%M:%S') + ' UTC'
                assert view['createdAtLabel'] == row['created_at'].strftime('%Y-%m-%d %H:%M:%S') + ' UTC'
            e['database_notifications'] = db
            e['lifecycle_counts'] = {table: c.execute(text(f'SELECT COUNT(*) FROM {table} WHERE work_order_id=:id'), {'id': ctx['history_order_id']}).scalar_one() for table in before['lifecycle_counts']}
            assert e['lifecycle_counts'] == before['lifecycle_counts']
            e['work_order'] = dict(c.execute(text('SELECT id,status,version FROM work_orders WHERE id=:id AND reporter_user_id=:user'), {'id': ctx['history_order_id'], 'user': me['user_id']}).mappings().one())
            assert e['work_order'] == before['work_order']
        queue = subprocess.run(['docker', 'exec', 'property-ai-rabbitmq-1', 'rabbitmqctl', 'list_queues', '-p', 'property_ai_auth001', 'name', 'messages_ready', 'messages_unacknowledged', 'consumers'], capture_output=True, text=True, timeout=30)
        assert queue.returncode == 0
        row = next(x.split() for x in queue.stdout.splitlines() if x.startswith('property_ai.auth001\t'))
        assert row[1:] == ['0', '0', '1']
        e['queue'] = {'vhost': 'property_ai_auth001', 'actual_output': queue.stdout, 'shared_access': False, 'message_content_read': False}
        e['status'] = 'PAGE_API_DB_PASS_PENDING_INDEPENDENT_REVIEW'
        e['limits'] = ['Runtime legacy wait-confirm message only; other exact templates and unknown text are pure unit projection tests.',
                       'No new event, lifecycle or delivery acceptance; existing current-run notifications preserved, historical lost013objects not reused.',
                       'Backend API/Worker remain same unchanged b75 source; no restart. External delivery text, Property-local-time presentation, physical device and production remain unverified.']
        print(json.dumps({'run_id': ctx['run_id'], 'pages': [len(x['items']) for x in e['api_pages']], 'target_notification': ctx['notification_id'], 'status': e['status']}))
    except Exception as error:
        e['error'] = type(error).__name__ + ': ' + str(error)
        raise
    finally:
        e['requests'] = client.requests
        e['observed_at_utc'] = datetime.now(UTC).isoformat()
        target.write_text(json.dumps(e, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
        client.close()
        engine.dispose()


if __name__ == '__main__':
    main()
