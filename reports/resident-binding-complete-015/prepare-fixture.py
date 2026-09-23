"""Additive 015 fixture using existing normal API; no DB business writes."""
from pathlib import Path
import json
import runpy
import secrets

from sqlalchemy import create_engine, text

REPO = Path(__file__).resolve().parents[2]
OUT = Path(__file__).parent
PRIVATE = REPO / 'tmp/resident-binding-complete-015/context.json'
helpers = runpy.run_path(str(REPO / 'reports/notification-events-inbox-013/run-runtime.py'))


def main():
    settings = helpers['_guard']()
    engine = create_engine(settings.test_database_url)
    with engine.connect() as conn:
        conn.execute(text('SET TRANSACTION READ ONLY'))
        assert conn.execute(text('SELECT version_num FROM alembic_version')).scalar_one() == '20260919_0009'
    engine.dispose()
    prior = json.loads((REPO / 'tmp/wo-reopen-receipt-history-014/context.json').read_text(encoding='utf-8'))
    PRIVATE.parent.mkdir(parents=True, exist_ok=True)
    ctx = json.loads(PRIVATE.read_text(encoding='utf-8')) if PRIVATE.exists() else {
        'run_id': 'bind015-' + secrets.token_hex(6), 'done': {}, 'identities': {},
        'phones': {'resident': f'192{secrets.randbelow(100_000_000):08d}', 'other': f'192{secrets.randbelow(100_000_000):08d}', 'manager': prior['phones']['manager']},
        'tenant_id': prior['tenant_id'], 'property_id': prior['property_id'], 'unit_id': prior['done']['unit'],
    }
    client = helpers['Client'](ctx['run_id'])
    def save():
        PRIVATE.write_text(json.dumps(ctx, ensure_ascii=False, indent=2), encoding='utf-8')
    def step(key, operation):
        if key not in ctx['done']:
            ctx['done'][key] = operation()
            save()
        return ctx['done'][key]
    save()
    try:
        principals = {}
        for actor in ('resident','other','manager'):
            tokens = step('login-'+actor, lambda: client.sms_login(ctx['phones'][actor], 'login-'+actor))
            me = client.data('GET','/me',token=tokens['access_token'],label='verify-'+actor)
            assert 'SYSTEM_ADMIN' not in me['roles']
            ctx['identities'][actor] = me['user_id']
            principals[actor] = {key: me[key] for key in ('user_id','roles','scopes')}
            if actor != 'manager':
                step('profile-'+actor, lambda: client.data('PATCH','/me/profile',token=tokens['access_token'],
                     body={'display_name': f'015-{actor}-{ctx["run_id"][-6:]}'},label='profile-'+actor))
        save()
        token = ctx['done']['login-manager']['access_token']
        # Inspect actual current hierarchy, then create only one identifiable house via API.
        units = client.data('GET', f'/buildings/{prior["done"]["building"]}/units',token=token,label='unit-check')
        assert any(row['id']==ctx['unit_id'] for row in units['items'])
        house = step('house', lambda: client.data('POST','/admin/houses',token=token,label='house-create',body={
            'tenant_id':ctx['tenant_id'],'property_id':ctx['property_id'],'unit_id':ctx['unit_id'],
            'room_no':'015-'+ctx['run_id'][-6:],'floor':1,
        }))
        ctx['house_id']=house['id']; save()
        evidence={'task_id':'RESIDENT-BINDING-COMPLETE-015','run_id':ctx['run_id'],
                  'base_sha':'0726d89203343a318a41e5f88c42eec316161d37',
                  'database':'127.0.0.1:3307/property_ai_auth001_test','revision':'20260919_0009',
                  'mode':'Normal SMS API authentication and authorized manager house API. Reuses the newly restored 014 organization, not lost historical objects. No direct SQL mutation.',
                  'identities':principals,'house':house,'requests':client.requests}
        (OUT/'fixture-evidence.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'run_id':ctx['run_id'],'house_id':ctx['house_id'],'status':'FIXTURE_READY'}))
    finally:
        client.close()


if __name__ == '__main__':
    main()
