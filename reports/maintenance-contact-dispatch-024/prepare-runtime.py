"""Checkpointed normal API setup; SQL only reads the named synthetic database."""
from datetime import UTC, datetime
import json
from pathlib import Path
import runpy
import secrets
import sys

ROOT = Path(__file__).resolve().parents[2]
h = runpy.run_path(str(ROOT/'reports/notification-events-inbox-013/run-runtime.py'))
from auth001_isolated_runtime import build_isolated_settings
from app.core.database import assert_isolated_auth_database, create_database_engine
from sqlalchemy import text

PRIVATE = ROOT/'tmp/maintenance-contact-dispatch-024/context.json'
OUT = Path(__file__).with_name('runtime-setup.json')
settings = build_isolated_settings()
assert_isolated_auth_database(settings, database_url=settings.active_database_url)
engine = create_database_engine(settings.active_database_url)
if PRIVATE.exists():
    ctx = json.loads(PRIVATE.read_text(encoding='utf-8'))
else:
    prior = json.loads((ROOT/'tmp/notification-presentation-021/context.json').read_text(encoding='utf-8'))
    manager_id = h['_principal_id'](prior['identities']['manager'])
    ctx = {'run_id': 'maint024-'+secrets.token_hex(5), 'tenant_id': prior['tenant_id'],
           'property_id': prior['property_id'], 'identities': {'manager': manager_id},
           'phones': {}, 'tokens': {}, 'steps': {}, 'last_sms_sent_at': {}}
    with engine.connect() as c:
        c.execute(text('SET TRANSACTION READ ONLY'))
        assert c.execute(text('SELECT version_num FROM alembic_version')).scalar_one() == '20260924_0011_contact_phone'
        # Only the already identified synthetic manager, never house-history numbers.
        ctx['phones']['manager'] = c.execute(text("SELECT phone_e164 FROM user_phones WHERE user_id=:id AND status='ACTIVE'"), {'id': manager_id}).scalar_one()
        public = {'run_id':ctx['run_id'], 'target':'127.0.0.1:3307/property_ai_auth001_test',
                  'revision':'20260924_0011_contact_phone', 'manager_id': manager_id,
                  'existing_staff': [dict(r) for r in c.execute(text('SELECT id,tenant_id,status,max_capacity FROM employees WHERE tenant_id=:tenant'), {'tenant':ctx['tenant_id']}).mappings()],
                  'prior_orders': [dict(r) for r in c.execute(text('SELECT id,status,version FROM work_orders ORDER BY id')).mappings()]}
    for label in ('resident', 'staff1', 'staff2', 'outsider'):
        ctx['phones'][label] = '199'+str(secrets.randbelow(100_000_000)).zfill(8)
    PRIVATE.write_text(json.dumps(ctx, ensure_ascii=False, indent=2), encoding='utf-8')
    OUT.write_text(json.dumps(public, ensure_ascii=False, indent=2), encoding='utf-8')
engine.dispose()
if '--prepare' not in sys.argv:
    print(json.dumps({'run_id':ctx['run_id'], 'mode':'readonly preflight; context saved privately'}))
    raise SystemExit()

client = h['Client'](ctx['run_id'])
public = json.loads(OUT.read_text(encoding='utf-8'))
public.setdefault('api_setup_requests', [])

def save():
    PRIVATE.write_text(json.dumps(ctx, ensure_ascii=False, indent=2), encoding='utf-8')
    public['api_setup_requests'].extend(client.requests)
    client.requests.clear()
    public['steps'] = {key: ('authentication stored privately' if key.startswith('login-') else value)
                       for key, value in ctx['steps'].items() if not key.startswith('invite-')}
    public['identities'] = ctx['identities']
    OUT.write_text(json.dumps(public, ensure_ascii=False, indent=2), encoding='utf-8')

def step(name, action):
    if name not in ctx['steps']:
        ctx['steps'][name] = action()
        save()
    return ctx['steps'][name]

def login(label):
    result = client.sms_login(ctx['phones'][label], label)
    ctx['tokens'][label] = result['access_token']
    principal = client.data('GET', '/me', token=result['access_token'], label=label+'-me')
    assert 'SYSTEM_ADMIN' not in principal['roles']
    if label == 'manager':
        assert principal['user_id'] == ctx['identities']['manager']
    ctx['identities'][label] = principal['user_id']
    ctx['last_sms_sent_at'][label] = int(datetime.now(UTC).timestamp()*1000)
    return {'user_id':principal['user_id'], 'roles':principal['roles']}

try:
    for label in ('manager','resident','staff1','staff2','outsider'):
        step('login-'+label, lambda label=label:login(label))
        step('profile-'+label, lambda label=label: client.data('PATCH','/me/profile',token=ctx['tokens'][label],body={'display_name':ctx['run_id']+'-'+label},label='profile-'+label)) if label != 'manager' else None
    token = ctx['tokens']['manager']
    scope = {'tenant_id':ctx['tenant_id'], 'property_id':ctx['property_id']}
    catalog = client.data('GET','/admin/employee-catalog',token=token,params=scope,label='catalog')
    maintenance = next((x for x in catalog['duties'] if x['code']=='MAINTENANCE'), None)
    if maintenance is None:
        maintenance = step('maintenance-duty', lambda: client.data('POST','/admin/duties',token=token,body={**scope,'code':'MAINTENANCE','name':'普通维修'},label='maintenance-duty'))
    ctx['duty_id'] = maintenance['id']
    for label in ('staff1','staff2'):
        employee = step('employee-'+label, lambda label=label:client.data('POST','/admin/employees',token=token,body={**scope,'employee_no':ctx['run_id']+'-'+label,'duty_ids':[ctx['duty_id']], 'skill_ids':[], 'max_capacity':1},label='employee-'+label))
        ctx.setdefault('employees', {})[label] = employee['id']
        invitation = step('invite-'+label,lambda label=label:client.data('POST',f"/admin/employees/{ctx['employees'][label]}/invites",token=token,body={'phone':ctx['phones'][label]},label='invite-'+label))
        step('claim-'+label,lambda label=label:client.data('POST','/staff/employee-claim',token=ctx['tokens'][label],body={'token':ctx['steps']['invite-'+label]['invite_token']},label='claim-'+label))
    # Distinct buildings prevent fabricated common-incident grouping of unrelated tests.
    for label in ('a','b','c'):
        building = step('building-'+label,lambda label=label:client.data('POST','/admin/buildings',token=token,body={**scope,'code':ctx['run_id']+'-'+label,'name':'024合成维修'+label},label='building-'+label))
        unit = step('unit-'+label,lambda label=label:client.data('POST','/admin/units',token=token,body={**scope,'building_id':ctx['steps']['building-'+label]['id'],'code':'01','name':'测试单元'},label='unit-'+label))
        house = step('house-'+label,lambda label=label:client.data('POST','/admin/houses',token=token,body={**scope,'unit_id':ctx['steps']['unit-'+label]['id'],'room_no':'2401','floor':24},label='house-'+label))
        ctx.setdefault('houses',{})[label] = house['id']
        binding = step('binding-'+label,lambda label=label:client.data('POST','/resident-bind-requests',token=ctx['tokens']['resident'],body={'house_id':ctx['houses'][label], 'relation_type':'TENANT'},label='binding-'+label))
        step('approve-'+label,lambda label=label:client.data('POST',f"/admin/resident-bind-requests/{ctx['steps']['binding-'+label]['id']}/approve",token=token,body={},label='approve-'+label))
    save()
    print(json.dumps({'run_id':ctx['run_id'],'mode':'normal API additive setup', 'employees':ctx['employees'],'houses':ctx['houses'], 'binding':'API requested and approved', 'attendance':'not performed yet'}))
finally:
    save()
    client.close()
