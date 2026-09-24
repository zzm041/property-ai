"""Additive synthetic setup, normal authentication/APIs; named Property fixture."""
import json
from pathlib import Path
import secrets
import sys
sys.path.insert(0,str(Path(__file__).parent))
from common import *

if not PRIVATE.exists():
    old=json.loads((ROOT/'tmp/maintenance-contact-dispatch-024/context.json').read_text(encoding='utf-8'))
    ctx={'run_id':'db025-'+secrets.token_hex(5),'tenant_id':old['tenant_id'],
         'identities':{'manager':old['identities']['manager']},
         'phones':{'manager':old['phones']['manager'],'resident':'199'+str(secrets.randbelow(100_000_000)).zfill(8),
                   'staff':'199'+str(secrets.randbelow(100_000_000)).zfill(8)},
         'tokens':{},'steps':{},'orders':{},'houses':{}}
    save_context(ctx)
ctx=read_context()
if '--init' in sys.argv:
    print(json.dumps({'run_id':ctx['run_id'],'new_objects_created':0,'mode':'private checkpoint initialized'}));raise SystemExit()
e_path=Path(__file__).with_name('setup.json')
e=json.loads(e_path.read_text(encoding='utf-8')) if e_path.exists() else {'run_id':ctx['run_id'],'steps':{},'requests':[]}
c=Client(ctx['run_id']);s=settings()
def save():
    save_context(ctx);e['requests'].extend(c.requests);c.requests.clear();public_write('setup.json',e)
def step(key,fn):
    if key not in ctx['steps']:
        ctx['steps'][key]=fn();save()
    return ctx['steps'][key]
try:
    for actor in ('manager','resident','staff'):
        if actor not in ctx['tokens']:
            ctx['tokens'][actor]=c.sms_login(ctx['phones'][actor],actor)['access_token'];save()
        me=c.data('GET','/me',token=ctx['tokens'][actor],label=actor+'-me')
        assert 'SYSTEM_ADMIN' not in me['roles'];ctx['identities'][actor]=me['user_id']
        if actor=='manager':assert any(g['role_code']=='TENANT_ADMIN' and g['tenant_id']==ctx['tenant_id'] for g in me['scopes'])
        else:step('profile-'+actor,lambda actor=actor:c.data('PATCH','/me/profile',token=ctx['tokens'][actor],body={'display_name':ctx['run_id']+'-'+actor},label='profile-'+actor))
    if 'property_id' not in ctx:
        from sqlalchemy.orm import Session
        from app.service.property import PropertyService
        engine=create_database_engine(s.active_database_url)
        with Session(engine,expire_on_commit=False) as session:
            prop=PropertyService(session).create_property(tenant_id=ctx['tenant_id'],name=ctx['run_id']+'故障恢复合成小区',address='025 isolated synthetic')
            ctx['property_id']=prop.id
        engine.dispose();save()
    token=ctx['tokens']['manager'];scope={'tenant_id':ctx['tenant_id'],'property_id':ctx['property_id']}
    catalog=c.data('GET','/admin/employee-catalog',token=token,params=scope,label='catalog')
    duty=next(x['id'] for x in catalog['duties'] if x['code']=='MAINTENANCE')
    employee=step('employee',lambda:c.data('POST','/admin/employees',token=token,body={**scope,'employee_no':ctx['run_id']+'-staff','duty_ids':[duty],'skill_ids':[],'max_capacity':1},label='employee'))
    ctx['employee_id']=employee['id'];save()
    invite=step('invite',lambda:c.data('POST',f"/admin/employees/{ctx['employee_id']}/invites",token=token,body={'phone':ctx['phones']['staff']},label='invite'))
    step('claim',lambda:c.data('POST','/staff/employee-claim',token=ctx['tokens']['staff'],body={'token':invite['invite_token']},label='claim'))
    # Distinct buildings: no accidental common-incident fixtures.
    for label in ('prepare','dispatch','busy','cancel','persistent','commit'):
        building=step('building-'+label,lambda label=label:c.data('POST','/admin/buildings',token=token,body={**scope,'code':ctx['run_id']+'-'+label,'name':'025 '+label},label='building-'+label))
        unit=step('unit-'+label,lambda label=label:c.data('POST','/admin/units',token=token,body={**scope,'building_id':building['id'],'code':'01','name':'合成单元'},label='unit-'+label))
        house=step('house-'+label,lambda label=label:c.data('POST','/admin/houses',token=token,body={**scope,'unit_id':unit['id'],'room_no':'2501','floor':25},label='house-'+label))
        ctx['houses'][label]=house['id'];save()
        bind=step('bind-'+label,lambda label=label:c.data('POST','/resident-bind-requests',token=ctx['tokens']['resident'],body={'house_id':house['id'],'relation_type':'TENANT'},label='bind-'+label))
        step('approve-'+label,lambda label=label:c.data('POST',f"/admin/resident-bind-requests/{bind['id']}/approve",token=token,body={},label='approve-'+label))
    step('attendance',lambda:c.data('POST','/staff/attendance/checkin',token=ctx['tokens']['staff'],body={'property_id':ctx['property_id']},label='attendance'))
    e['steps']={'normal_auth':True,'manager_role':'TENANT_ADMIN','property_fixture':'existing additive PropertyService (not UI)',
                'property_id':ctx['property_id'],'employee_id':ctx['employee_id'],'duties':['MAINTENANCE'],'skill_ids':[],
                'capacity':1,'invite_claim':'normal API','houses':ctx['houses'],'business_orders':0}
    save();print(json.dumps({'run_id':ctx['run_id'],'property_id':ctx['property_id'],'employee_id':ctx['employee_id'],'orders':0}))
finally:save();c.close()
