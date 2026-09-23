"""015 authenticated negative APIs and SELECT-only persisted evidence."""
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from threading import Barrier
import json
import runpy
import subprocess

from sqlalchemy import create_engine, text

REPO = Path(__file__).resolve().parents[2]
OUT = Path(__file__).parent
PRIVATE = REPO / 'tmp/resident-binding-complete-015/context.json'
helpers = runpy.run_path(str(REPO / 'reports/notification-events-inbox-013/run-runtime.py'))


def main():
    settings = helpers['_guard']()
    ctx = json.loads(PRIVATE.read_text(encoding='utf-8'))
    page = json.loads((OUT/'page-evidence.json').read_text(encoding='utf-8'))
    assert page['status'] == 'PAGE_PASS_PENDING_INDEPENDENT_REVIEW'
    client = helpers['Client'](ctx['run_id'])
    tokens = {actor:ctx['done']['login-'+actor]['access_token'] for actor in ('resident','other','manager')}
    evidence_path=OUT/'api-database-queue-evidence.json'
    evidence=json.loads(evidence_path.read_text(encoding='utf-8')) if evidence_path.exists() else {
        'task_id':'RESIDENT-BINDING-COMPLETE-015','run_id':ctx['run_id'],'code_sha':page['code_sha'],
        'steps':{},'requests':[], 'database':'127.0.0.1:3307/property_ai_auth001_test',
        'mode':'Authenticated API negative cases, not UI; SELECT-only SQL; unique queue metadata, no message content.',
    }
    def save():
        evidence['requests'].extend(client.requests);client.requests.clear()
        evidence_path.write_text(json.dumps(evidence,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    def step(label,op):
        if label not in evidence['steps']:
            evidence['steps'][label]=op();save()
        return evidence['steps'][label]
    def call(method,path,actor,label,body=None,expected=(200,)):
        status,payload=client.call(method,path,token=tokens[actor],label=label,body=body,expected=expected)
        return {'status':status,'error':payload.get('error'),'data':payload.get('data')}
    def negative(label,path,actor,body,expected,code):
        result=step(label,lambda:call('POST',path,actor,label,body,(expected,)))
        assert result['error']['code']==code
    try:
        path='/resident-bind-requests'; house=ctx['house_id']
        negative('invalid-relation',path,'other',{'house_id':house,'relation_type':'GUEST'},422,'SYS.VALIDATION_ERROR')
        negative('forged-user',path,'other',{'house_id':house,'relation_type':'TENANT','user_id':ctx['identities']['resident']},422,'SYS.VALIDATION_ERROR')
        negative('false-scope',path,'other',{'house_id':house,'relation_type':'TENANT','property_id':ctx['property_id']+100000},422,'RES.INVALID_INPUT')
        negative('duplicate-active',path,'resident',{'house_id':house,'relation_type':'FAMILY'},409,'RES.DUPLICATE_ACTIVE_RELATION')
        def concurrent_apply():
            before=client.data('GET','/resident-bind-requests/mine',token=tokens['other'],label='before-race')
            assert not any(row['house_id']==house and row['status']=='PENDING' for row in before['items']), 'Existing pending request: inspect and resume; do not repeat concurrent mutation'
            barrier=Barrier(2)
            def send(n):
                barrier.wait(timeout=10)
                return call('POST',path,'other',f'concurrent-{n}',{'house_id':house,'relation_type':'FAMILY'},(200,409))
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures=[pool.submit(send,n) for n in (1,2)]
                return [future.result() for future in futures]
        race=step('concurrent-apply',concurrent_apply)
        assert sorted(row['status'] for row in race)==[200,409]
        assert next(row for row in race if row['status']==409)['error']['code']=='RES.DUPLICATE_PENDING'
        pending=next(row['data'] for row in race if row['status']==200)
        negative('duplicate-pending-other-type',path,'other',{'house_id':house,'relation_type':'AUTHORIZED_OTHER'},409,'RES.DUPLICATE_PENDING')
        review=f'/admin/resident-bind-requests/{pending["id"]}'
        negative('resident-cannot-approve',review+'/approve','resident',None,403,'IAM.FORBIDDEN')
        negative('resident-cannot-reject',review+'/reject','resident',{'reason':'not authorized'},403,'IAM.FORBIDDEN')
        negative('empty-review-reason',review+'/reject','manager',{'reason':'  '},422,'RES.INVALID_INPUT')
        other=step('other-history',lambda:call('GET','/resident-bind-requests/mine','other','other-history'))['data']['items']
        assert all(row['user_id']==ctx['identities']['other'] for row in other)
        assert not any(row['id'] in [ctx['first_request_id'],ctx['second_request_id']] for row in other)
        step('finish-negative-fixture',lambda:call('POST',review+'/reject','manager','finish-negative-fixture',{'reason':ctx['run_id']+' API negative fixture complete'}))
        negative('repeat-rejection',review+'/reject','manager',{'reason':'must not replace prior reason'},409,'RES.ALREADY_PROCESSED')
        negative('repeat-approval',f'/admin/resident-bind-requests/{ctx["second_request_id"]}/approve','manager',None,409,'RES.ALREADY_PROCESSED')
        engine=create_engine(settings.test_database_url)
        try:
            with engine.connect() as conn:
                conn.execute(text('SET TRANSACTION READ ONLY'))
                evidence['revision']=conn.execute(text('SELECT version_num FROM alembic_version')).scalar_one()
                assert evidence['revision']=='20260919_0009'
                rows=[dict(row) for row in conn.execute(text('SELECT id,user_id,tenant_id,property_id,house_id,relation_type,status,reviewer_id,review_reason,requested_at,reviewed_at FROM resident_bind_requests WHERE house_id=:house ORDER BY id'),{'house':house}).mappings()]
                relations=[dict(row) for row in conn.execute(text('SELECT id,user_id,house_id,relation_type,status,bind_request_id,valid_from,valid_to FROM resident_house_relations WHERE house_id=:house ORDER BY id'),{'house':house}).mappings()]
                audits=[dict(row) for row in conn.execute(text("SELECT id,actor_user_id,action,resource_type,resource_id,tenant_id,property_id,created_at FROM audit_logs WHERE resource_type='RESIDENT_BIND_REQUEST' AND resource_id IN (:first,:second,:third) ORDER BY id"),{'first':ctx['first_request_id'],'second':ctx['second_request_id'],'third':pending['id']}).mappings()]
                assert len(rows)==3 and len(relations)==1
                first=next(row for row in rows if row['id']==ctx['first_request_id'])
                second=next(row for row in rows if row['id']==ctx['second_request_id'])
                assert first['status']=='REJECTED' and first['review_reason']==ctx['reject_reason']
                assert second['status']=='APPROVED' and second['relation_type']=='TENANT'
                assert first['reviewer_id']==second['reviewer_id']==ctx['identities']['manager']
                assert relations[0]['bind_request_id']==second['id'] and relations[0]['relation_type']=='TENANT' and relations[0]['status']=='ACTIVE'
                assert len(audits)==6
                evidence['persisted']={'requests':rows,'relations':relations,'audits':audits}
        finally:
            engine.dispose()
        queue=subprocess.run(['docker','exec','property-ai-rabbitmq-1','rabbitmqctl','list_queues','-p','property_ai_auth001','name','messages_ready','messages_unacknowledged','consumers'],capture_output=True,text=True,timeout=30)
        assert queue.returncode==0
        evidence['queue']={'vhost':'property_ai_auth001','actual_output':queue.stdout,'message_content_read':False,'shared_vhost_accessed':False}
        evidence['collected_at_utc']=datetime.now(UTC).isoformat();evidence['status']='API_DB_PASS_PENDING_INDEPENDENT_REVIEW';save()
        print(json.dumps({'run_id':ctx['run_id'],'status':evidence['status'],'request_ids':[row['id'] for row in rows],'relations':len(relations),'audits':len(audits)}))
    finally:
        save();client.close()


if __name__=='__main__':
    main()
