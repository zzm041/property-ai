"""Reuse verified current AUTH001 synthetic identities; never recreate old passed flows."""
from pathlib import Path
from datetime import UTC, datetime
import json
import runpy
import secrets
import subprocess

REPO = Path(__file__).resolve().parents[2]
OUT = Path(__file__).parent
PRIVATE = REPO / 'tmp/manager-wo-cancel-017/context.json'
helpers = runpy.run_path(str(REPO / 'reports/notification-events-inbox-013/run-runtime.py'))
from sqlalchemy import create_engine, text


def main():
    settings = helpers['_guard']()
    prior = json.loads((REPO / 'tmp/wo-reopen-receipt-history-014/context.json').read_text(encoding='utf-8'))
    PRIVATE.parent.mkdir(parents=True, exist_ok=True)
    ctx = json.loads(PRIVATE.read_text(encoding='utf-8')) if PRIVATE.exists() else {
        'run_id': 'cancel017-' + secrets.token_hex(6), 'done': {}, 'last_sms_sent_at': {},
        **{key: prior[key] for key in ('tenant_id', 'property_id', 'house_id', 'phones', 'identities', 'employees')},
    }
    def save():
        PRIVATE.write_text(json.dumps(ctx, ensure_ascii=False, indent=2), encoding='utf-8')
    save()
    engine = create_engine(settings.test_database_url)
    client = helpers['Client'](ctx['run_id'])
    result = {'task_id': 'MANAGER-WO-CANCEL-017', 'run_id': ctx['run_id'],
              'code_sha': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=REPO, text=True).strip(),
              'identities': ctx['identities'], 'employees': ctx['employees'],
              'tenant_id': ctx['tenant_id'], 'property_id': ctx['property_id'], 'house_id': ctx['house_id'],
              'mode': 'Retained current014 synthetic identities checked live; normal SMS authentication, GET qualification; no tenant/role/invite/binding recreation.'}
    try:
        with engine.connect() as conn:
            conn.execute(text('SET TRANSACTION READ ONLY'))
            result['revision'] = conn.execute(text('SELECT version_num FROM alembic_version')).scalar_one()
            assert result['revision'] == '20260919_0009'
            result['house'] = dict(conn.execute(text('SELECT id,tenant_id,property_id,status,room_no FROM houses WHERE id=:id'), {'id': ctx['house_id']}).mappings().one())
            assert result['house']['status'] == 'ACTIVE' and result['house']['property_id'] == ctx['property_id']
            relation = conn.execute(text("SELECT count(*) FROM resident_house_relations WHERE house_id=:house AND user_id=:user AND status='ACTIVE'"), {'house': ctx['house_id'], 'user': ctx['identities']['resident']}).scalar_one()
            assert relation > 0
            result['resident_active_relation_count'] = relation
        for actor in ('manager', 'resident', 'other', 'staff', 'backup'):
            key = 'login-' + actor
            if key not in ctx['done']:
                ctx['done'][key] = client.sms_login(ctx['phones'][actor], key)
                ctx['last_sms_sent_at'][actor] = datetime.now(UTC).timestamp() * 1000
                save()
            me = client.data('GET', '/me', token=ctx['done'][key]['access_token'], label='verify-' + actor)
            assert me['user_id'] == ctx['identities'][actor] and 'SYSTEM_ADMIN' not in me['roles']
        result['staff_before'] = {}
        for actor in ('staff', 'backup'):
            token = ctx['done']['login-' + actor]['access_token']
            today = client.data('GET', f'/staff/attendance/today?property_id={ctx["property_id"]}', token=token, label='today-' + actor)
            result['staff_before'][actor] = today
            daily = today.get('daily_status')
            assert daily and daily['employee_id'] == ctx['employees'][actor] and daily['on_duty'] == 'ON_DUTY', 'Qualification changed; inspect before any attendance mutation'
            assert daily['current_load'] == 0, 'Existing employee load not zero; inspect before continuing'
        result['requests'] = client.requests
        result['status'] = 'CURRENT_FIXTURE_CHECKED_NO_BUSINESS_RECREATION'
        (OUT / 'fixture-evidence.json').write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
        print(json.dumps({'run_id': ctx['run_id'], 'status': result['status'], 'identities': ctx['identities']}))
    finally:
        client.close()
        engine.dispose()


if __name__ == '__main__':
    main()
