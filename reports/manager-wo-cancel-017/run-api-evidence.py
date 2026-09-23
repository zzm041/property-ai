"""017 normal authenticated API negatives/races; read-only DB correspondence."""
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from threading import Barrier
import json
import runpy
import subprocess

REPO = Path(__file__).resolve().parents[2]
OUT = Path(__file__).parent
PRIVATE = REPO / 'tmp/manager-wo-cancel-017/context.json'
helpers = runpy.run_path(str(REPO / 'reports/notification-events-inbox-013/run-runtime.py'))
import httpx
from sqlalchemy import create_engine, text, bindparam


def main():
    settings = helpers['_guard']()
    ctx = json.loads(PRIVATE.read_text(encoding='utf-8'))
    page = json.loads((OUT / 'page-evidence.json').read_text(encoding='utf-8'))
    assert page['status'] == 'PAGE_PASS_PENDING_INDEPENDENT_REVIEW'
    client = helpers['Client'](ctx['run_id'] + '-api')
    engine = create_engine(settings.test_database_url)
    path = OUT / 'api-database-evidence.json'
    state = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {
        'task_id': 'MANAGER-WO-CANCEL-017', 'run_id': ctx['run_id'], 'code_sha': page['code_sha'],
        'mode': 'Normal API tests, real MySQL two-request races, SELECT-only persistence; not UI claims.',
        'results': {}, 'requests': [], 'objects': {'page': ctx['page_order_id']},
    }
    tokens = {actor: ctx['done']['login-' + actor]['access_token'] for actor in ctx['identities']}
    def save():
        state['requests'].extend(client.requests)
        client.requests.clear()
        path.write_text(json.dumps(state, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    def call(method, endpoint, *, actor='resident', body=None, label, expected=(200,), headers=None):
        return client.call(method, endpoint, token=tokens[actor], body=body, label=label, expected=expected, headers=headers)
    def data(method, endpoint, **kwargs):
        return call(method, endpoint, **kwargs)[1]['data']
    def detail(order_id):
        return data('GET', f'/work-orders/{order_id}', label=f'detail-{order_id}')
    def step(label, operation):
        if label not in state['results']:
            state['results'][label] = operation()
            save()
        return state['results'][label]
    def create(label):
        created = step(label + '-create', lambda: data('POST', '/work-orders', label=label + '-create',
                       body={'house_id': ctx['house_id'], 'text': ctx['run_id'] + '-' + label + ' 电气灯具故障'},
                       headers={'Idempotency-Key': ctx['run_id'] + '-' + label}))
        order_id = created['id']
        state['objects'][label] = order_id
        if label + '-assigned' not in state['results']:
            assigned = helpers['_wait_assigned'](client, tokens['resident'], order_id)
            a = assigned['assignments'][-1]
            assert a['reason'] == 'AUTO_DISPATCH' and a['assigned_by_user_id'] is None
            staff = next(k for k, v in ctx['employees'].items() if v == a['employee_id'])
            state['results'][label + '-assigned'] = {'detail': assigned, 'staff': staff}
            save()
        return order_id, state['results'][label + '-assigned']['staff']
    def transition(order_id, actor, action, label):
        row = detail(order_id)['work_order']
        return data('POST', f'/staff/work-orders/{order_id}/{action}', actor=actor, label=label, body={'version': row['version']})
    def race(order_id, staff, actions, label):
        before = detail(order_id)
        version = before['work_order']['version']
        barrier = Barrier(2)
        def one(index, action):
            who = 'manager' if action == 'cancel' else staff
            prefix = '' if action == 'cancel' else '/staff'
            body = {'version': version}
            if action == 'cancel':
                body['reason'] = ctx['run_id'] + '-' + label
            barrier.wait(timeout=10)
            response = httpx.post(f'http://127.0.0.1:8083/api/v1{prefix}/work-orders/{order_id}/{action}',
                headers={'Authorization': 'Bearer ' + tokens[who], 'X-Request-Id': ctx['run_id'] + '-' + label + '-' + str(index)},
                json=body, timeout=30)
            payload = response.json()
            return {'action': action, 'status': response.status_code, 'request_id': payload.get('request_id'),
                    'error_code': helpers['_error_code'](payload)}
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(one, i, action) for i, action in enumerate(actions)]
            responses = [f.result() for f in futures]
        after = detail(order_id)
        # Persist actual outcomes before asserting, never silently replay a winner.
        result = {'before': before, 'responses': responses, 'after': after, 'parallel_start': 'two futures + barrier'}
        state['results'][label] = result
        save()
        assert sorted(r['status'] for r in responses) == [200, 409]
        assert after['work_order']['version'] == version + 1
        return result
    def finish(order_id, staff, label):
        row = detail(order_id)['work_order']
        if row['status'] == 'PROCESSING':
            row = data('POST', f'/staff/work-orders/{order_id}/receipts', actor=staff, label=label + '-receipt', body={
                'version': row['version'], 'problem_type': 'ELECTRICAL', 'method': ctx['run_id'] + ' checked wiring',
                'fee_flag': 'NONE', 'resolved': True, 'after_file_ids': []})
        if row['status'] == 'WAIT_CONFIRM':
            row = data('POST', f'/work-orders/{order_id}/confirm', label=label + '-confirm', body={'version': row['version']})
        assert row['status'] in ('CANCELLED', 'CLOSED')
        return row
    try:
        # Real DB has current version and only named isolated target is used.
        with engine.connect() as conn:
            conn.execute(text('SET TRANSACTION READ ONLY'))
            state['revision'] = conn.execute(text('SELECT version_num FROM alembic_version')).scalar_one()
            assert state['revision'] == '20260919_0009'
        for actor in tokens:
            me = data('GET', '/me', actor=actor, label='identity-' + actor)
            assert me['user_id'] == ctx['identities'][actor] and 'SYSTEM_ADMIN' not in me['roles']
        page_detail = detail(ctx['page_order_id'])
        assert page_detail['work_order']['status'] == 'CANCELLED'
        assert page_detail['work_order']['current_assignment_id'] is None
        assert all(a['status'] == 'ENDED' for a in page_detail['assignments'])
        state['page_final_detail'] = page_detail
        old_version = page['steps']['02-manager-confirmation']['selectedVersion']
        step('page-duplicate', lambda: call('POST', f'/work-orders/{ctx["page_order_id"]}/cancel', actor='manager',
             label='page-duplicate', body={'version': old_version}, expected=(409,))[0])
        queue = f'/supervisor/work-orders?property_id={ctx["property_id"]}&view=unstarted'
        for actor in ('resident', 'other', 'staff'):
            step('queue-denied-' + actor, lambda actor=actor: call('GET', queue, actor=actor, label='queue-denied-' + actor, expected=(403,))[0])
        for suffix in ('&limit=0', '&limit=101', '&after_id=-1'):
            step('queue-invalid-' + suffix, lambda suffix=suffix: call('GET', queue + suffix, actor='manager', label='queue-invalid-' + suffix, expected=(422,))[0])

        order_id, staff = create('accepted-race')
        step('accepted-race-accept', lambda: transition(order_id, staff, 'accept', 'accepted-race-accept'))
        for actor in ('other', staff):
            step('cancel-denied-' + actor, lambda actor=actor: call('POST', f'/work-orders/{order_id}/cancel', actor=actor,
                 body={'version': detail(order_id)['work_order']['version']}, label='cancel-denied-' + actor, expected=(403,))[0])
        step('accepted-race', lambda: race(order_id, staff, ['cancel', 'cancel'], 'accepted-race'))
        assert detail(order_id)['work_order']['status'] == 'CANCELLED'

        order_id, staff = create('illegal-processing')
        step('illegal-accept', lambda: transition(order_id, staff, 'accept', 'illegal-accept'))
        step('illegal-start', lambda: transition(order_id, staff, 'start', 'illegal-start'))
        step('processing-cancel-denied', lambda: call('POST', f'/work-orders/{order_id}/cancel', actor='manager',
             body={'version': detail(order_id)['work_order']['version']}, label='processing-cancel-denied', expected=(409,))[0])
        def waiting():
            row = detail(order_id)['work_order']
            if row['status'] == 'PROCESSING':
                return data('POST', f'/staff/work-orders/{order_id}/receipts', actor=staff, label='waiting-receipt', body={
                    'version': row['version'], 'problem_type': 'ELECTRICAL', 'method': ctx['run_id'] + ' negative state test completion',
                    'fee_flag': 'NONE', 'resolved': True, 'after_file_ids': []})
            assert row['status'] == 'WAIT_CONFIRM'
            return row
        step('waiting', waiting)
        step('waiting-cancel-denied', lambda: call('POST', f'/work-orders/{order_id}/cancel', actor='manager',
             body={'version': detail(order_id)['work_order']['version']}, label='waiting-cancel-denied', expected=(409,))[0])
        step('illegal-final', lambda: finish(order_id, staff, 'illegal-final'))
        step('closed-cancel-denied', lambda: call('POST', f'/work-orders/{order_id}/cancel', actor='manager',
             body={'version': detail(order_id)['work_order']['version']}, label='closed-cancel-denied', expected=(409,))[0])

        order_id, staff = create('cancel-vs-start')
        step('start-race-accept', lambda: transition(order_id, staff, 'accept', 'start-race-accept'))
        step('cancel-vs-start', lambda: race(order_id, staff, ['cancel', 'start'], 'cancel-vs-start'))
        step('start-race-final', lambda: finish(order_id, staff, 'start-race-final'))

        with engine.connect() as conn:
            conn.execute(text('SET TRANSACTION READ ONLY'))
            ids = list(state['objects'].values())
            def rows(query):
                return [dict(r) for r in conn.execute(text(query).bindparams(bindparam('ids', expanding=True)), {'ids': ids}).mappings()]
            orders = rows('SELECT id,tenant_id,property_id,reporter_user_id,status,current_assignment_id,version,incident_id FROM work_orders WHERE id IN :ids ORDER BY id')
            assignments = rows('SELECT id,work_order_id,employee_id,status,assigned_by_user_id,assigned_at,ended_at,reason FROM work_order_assignments WHERE work_order_id IN :ids ORDER BY id')
            logs = rows('SELECT id,work_order_id,actor_user_id,actor_type,from_status,to_status,event_code,payload_json FROM work_order_logs WHERE work_order_id IN :ids ORDER BY id')
            audits = rows("SELECT id,action,actor_user_id,resource_id,request_id FROM audit_logs WHERE resource_type='WORK_ORDER' AND resource_id IN :ids ORDER BY id")
            notifications = rows("SELECT id,user_id,resource_id,title,read_at FROM notifications WHERE resource_type='WORK_ORDER' AND resource_id IN :ids ORDER BY id")
            clocks = rows('SELECT work_order_id,state FROM sla_clocks WHERE work_order_id IN :ids ORDER BY work_order_id')
            daily = [dict(r) for r in conn.execute(text('SELECT employee_id,on_duty,current_load,business_date FROM employee_daily_statuses WHERE property_id=:id'), {'id': ctx['property_id']}).mappings()]
            assert all(r['status'] in ('CANCELLED', 'CLOSED') and r['current_assignment_id'] is None for r in orders)
            assert all(r['status'] == 'ENDED' and r['ended_at'] for r in assignments)
            assert all(r['state'] == 'STOPPED' for r in clocks) and all(r['current_load'] == 0 for r in daily)
            for row in orders:
                cancel_logs = [r for r in logs if r['work_order_id'] == row['id'] and r['event_code'] == 'CANCEL']
                assert len(cancel_logs) == (1 if row['status'] == 'CANCELLED' else 0)
                if cancel_logs:
                    assert cancel_logs[0]['actor_user_id'] == ctx['identities']['manager']
                    assert len([r for r in audits if r['resource_id'] == row['id'] and r['action'] == 'WORK_ORDER_CANCELLED']) == 1
                    assignment = next(r for r in assignments if r['work_order_id'] == row['id'])
                    who = next(k for k, v in ctx['employees'].items() if v == assignment['employee_id'])
                    recipients = {r['user_id'] for r in notifications if r['resource_id'] == row['id'] and r['title'] == '工单已撤销'}
                    assert recipients == {ctx['identities']['resident'], ctx['identities'][who]}
            state['database'] = {'target': '127.0.0.1:3307/property_ai_auth001_test', 'orders': orders, 'assignments': assignments,
                'logs': logs, 'audits': audits, 'notifications': notifications, 'sla_clocks': clocks, 'daily_load': daily}
        queue_output = subprocess.run(['docker','exec','property-ai-rabbitmq-1','rabbitmqctl','list_queues','-p','property_ai_auth001',
                                      'name','messages_ready','messages_unacknowledged','consumers'], capture_output=True, text=True, timeout=30)
        assert queue_output.returncode == 0
        state['queue'] = {'vhost': 'property_ai_auth001', 'actual_output': queue_output.stdout, 'message_content_read': False, 'shared_vhost_accessed': False}
        state['collected_at_utc'] = datetime.now(UTC).isoformat()
        state['status'] = 'API_DB_PASS_PENDING_REVIEW'
        save()
        print(json.dumps({'run_id': ctx['run_id'], 'orders': state['objects'], 'status': state['status']}))
    finally:
        save()
        client.close()
        engine.dispose()


if __name__ == '__main__':
    main()
