"""Resume only unfinished negative GETs; preserve the immutable positive DB run."""
from pathlib import Path
from datetime import UTC, datetime
import hashlib
import json
import runpy
import subprocess

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).parent
h = runpy.run_path(str(ROOT / 'reports/notification-events-inbox-013/run-runtime.py'))


def main():
    h['_guard']()
    prior_path = OUT / 'api-database-evidence.json'
    prior = json.loads(prior_path.read_text(encoding='utf-8'))
    assert prior['error'] == 'RuntimeError: cross-user-1 failed: HTTP 403 code=IAM.FORBIDDEN'
    assert len(prior['orders']) == 2 and prior['revision'] == '20260919_0009'
    ctx = json.loads((ROOT / 'tmp/resident-wo-presentation-020/context.json').read_text(encoding='utf-8'))
    assert ctx['run_id'] == prior['run_id'] and ctx.get('other_token')
    target = OUT / 'api-negatives-resume.json'
    assert not target.exists(), 'Preserve completed checkpoint; no automatic replay'
    e = {'task_id': prior['task_id'], 'run_id': ctx['run_id'], 'product_sha': prior['product_sha'],
         'resumes': prior_path.name, 'prior_sha256': hashlib.sha256(prior_path.read_bytes()).hexdigest(),
         'interruption': prior['error'], 'negative': {},
         'basis': 'docs/06-API-CONTRACT.md and WorkOrderService._authorize_read require 403 IAM.FORBIDDEN. First failed expectation retained; one read-only GET repeated to capture response body. No product change, positive DB/UI replay or new login.'}
    client = h['Client'](ctx['run_id'])
    try:
        me = client.data('GET', '/me', token=ctx['other_token'], label='resume-other-principal')
        assert me['user_id'] == ctx['identities']['other']
        e['principal'] = {'user_id': me['user_id'], 'roles': me['roles']}
        for order_id in (ctx['history_order_id'], ctx['active_order_id']):
            status, payload = client.call('GET', f'/work-orders/{order_id}', token=ctx['other_token'],
                                          label=f'resume-cross-user-{order_id}', expected=(403,))
            assert payload.get('data') is None and payload['error']['code'] == 'IAM.FORBIDDEN'
            assert all(key not in payload for key in ('display', 'work_order', 'assignee_names', 'processes', 'receipts'))
            e['negative'][str(order_id)] = {'status': status, 'response': payload, 'business_metadata_leaked': False}
        status, payload = client.call('GET', f'/work-orders/{ctx["history_order_id"]}', label='resume-anonymous', expected=(401,))
        assert payload.get('data') is None
        e['anonymous'] = {'status': status, 'response': payload}
        result = subprocess.run(['docker', 'exec', 'property-ai-rabbitmq-1', 'rabbitmqctl', 'list_queues',
                                 '-p', 'property_ai_auth001', 'name', 'messages_ready', 'messages_unacknowledged', 'consumers'],
                                capture_output=True, text=True, timeout=30)
        assert result.returncode == 0
        row = next(x.split() for x in result.stdout.splitlines() if x.startswith('property_ai.auth001\t'))
        assert row[1:] == ['0', '0', '1']
        e['queue'] = {'vhost': 'property_ai_auth001', 'actual_output': result.stdout,
                      'shared_access': False, 'message_content_read': False}
        e['status'] = 'API_DB_READONLY_PASS_PENDING_INDEPENDENT_REVIEW'
        e['limits'] = ['Own API and SELECT-only DB assertions are in immutable prior file; no successful operation replayed.',
                       'Runtime Asia/Shanghai, same-tenant nonowner 403, anonymous 401; DST and foreign-scope SQL are unit/source evidence.',
                       'Current names are not historical name snapshots. No new lifecycle records or fixture created.']
        print(json.dumps({'run_id': ctx['run_id'], 'status': e['status'], 'negative_statuses': [x['status'] for x in e['negative'].values()]}))
    except Exception as error:
        e['error'] = type(error).__name__ + ': ' + str(error)
        raise
    finally:
        e['requests'] = client.requests
        e['observed_at_utc'] = datetime.now(UTC).isoformat()
        target.write_text(json.dumps(e, ensure_ascii=False, indent=2), encoding='utf-8')
        client.close()


if __name__ == '__main__':
    main()
