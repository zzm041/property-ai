"""Finite additive API-only pagination fixture; no destructive test fixture."""
from pathlib import Path
import json
import runpy
import secrets
from sqlalchemy import create_engine, text

REPO=Path(__file__).resolve().parents[2]
OUT=Path(__file__).parent
PRIVATE=REPO/'tmp/house-catalog-paging-016/context.json'
helpers=runpy.run_path(str(REPO/'reports/notification-events-inbox-013/run-runtime.py'))


def main():
    settings=helpers['_guard']()
    engine=create_engine(settings.test_database_url)
    with engine.connect() as connection:
        connection.execute(text('SET TRANSACTION READ ONLY'))
        assert connection.execute(text('SELECT version_num FROM alembic_version')).scalar_one()=='20260919_0009'
    engine.dispose()
    prior=json.loads((REPO/'tmp/resident-binding-complete-015/context.json').read_text(encoding='utf-8'))
    PRIVATE.parent.mkdir(parents=True,exist_ok=True)
    ctx=json.loads(PRIVATE.read_text(encoding='utf-8')) if PRIVATE.exists() else {
        'run_id':'paging016-'+secrets.token_hex(6),'done':{},'phones':prior['phones'],'identities':prior['identities'],
        'tenant_id':prior['tenant_id'],'property_id':prior['property_id'],'unit_id':prior['unit_id'],
    }
    client=helpers['Client'](ctx['run_id'])
    def save():
        PRIVATE.write_text(json.dumps(ctx,ensure_ascii=False,indent=2),encoding='utf-8')
    def step(label,operation):
        if label not in ctx['done']:
            ctx['done'][label]=operation();save()
        return ctx['done'][label]
    def api(method,path,actor,label,body=None):
        return step(label,lambda:client.data(method,path,token=ctx['done']['login-'+actor]['access_token'],label=label,body=body))
    save()
    try:
        for actor in ('manager','resident','other'):
            token=step('login-'+actor,lambda:client.sms_login(ctx['phones'][actor],'login-'+actor))
            me=client.data('GET','/me',token=token['access_token'],label='verify-'+actor)
            assert me['user_id']==ctx['identities'][actor] and 'SYSTEM_ADMIN' not in me['roles']
        ctx['houses']=[];ctx['active_requests']=[];ctx['pending_requests']=[]
        for index in range(51):
            house=api('POST','/admin/houses','manager',f'house-{index}',{
                'tenant_id':ctx['tenant_id'],'property_id':ctx['property_id'],'unit_id':ctx['unit_id'],
                'room_no':f'016-{ctx["run_id"][-6:]}-{index:02d}','floor':1,
            })
            ctx['houses'].append(house['id'])
            if index<50:
                application=api('POST','/resident-bind-requests','resident',f'active-apply-{index}',{'house_id':house['id'],'relation_type':'TENANT'})
                api('POST',f'/admin/resident-bind-requests/{application["id"]}/approve','manager',f'active-approve-{index}')
                ctx['active_requests'].append(application['id'])
            application=api('POST','/resident-bind-requests','other',f'pending-apply-{index}',{'house_id':house['id'],'relation_type':'FAMILY'})
            ctx['pending_requests'].append(application['id']);save()
            if index%10==0:print(json.dumps({'run_id':ctx['run_id'],'prepared_houses':index+1}),flush=True)
        ctx['target_house_id']=ctx['houses'][-1];save()
        evidence={'task_id':'HOUSE-CATALOG-PAGING-016','run_id':ctx['run_id'],'code_sha':'b54442e219e9d8e9e8e569f2afaec8d682169a8f',
                  'mode':'Finite pagination fixture:51 new houses,50 approved resident relations and51 other-user pending requests through normal APIs. Existing015 identities reused via fresh normal SMS authentication. No SQL mutation or old passed flow replay.',
                  'database':'127.0.0.1:3307/property_ai_auth001_test','revision':'20260919_0009',
                  'tenant_id':ctx['tenant_id'],'property_id':ctx['property_id'],'unit_id':ctx['unit_id'],'identities':ctx['identities'],
                  'houses':ctx['houses'],'active_requests':ctx['active_requests'],'pending_requests':ctx['pending_requests'],
                  'target_house_id':ctx['target_house_id'],'requests':client.requests}
        (OUT/'fixture-evidence.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'run_id':ctx['run_id'],'status':'FIXTURE_READY','houses':len(ctx['houses'])}))
    finally:
        client.close()


if __name__=='__main__':main()
