"""Read-only API/DB/queue correspondence after actual016 page evidence."""
from pathlib import Path
from datetime import UTC, datetime
import json
import runpy
import subprocess
from sqlalchemy import create_engine, text, bindparam

REPO=Path(__file__).resolve().parents[2]
OUT=Path(__file__).parent
helpers=runpy.run_path(str(REPO/'reports/notification-events-inbox-013/run-runtime.py'))


def main():
    settings=helpers['_guard']()
    ctx=json.loads((REPO/'tmp/house-catalog-paging-016/context.json').read_text(encoding='utf-8'))
    page=json.loads((OUT/'page-evidence.json').read_text(encoding='utf-8'))
    assert page['status']=='PAGE_PASS_PENDING_INDEPENDENT_REVIEW'
    client=helpers['Client'](ctx['run_id'])
    result={'task_id':'HOUSE-CATALOG-PAGING-016','run_id':ctx['run_id'],'code_sha':page['code_sha'],
            'database':'127.0.0.1:3307/property_ai_auth001_test','mode':'GET-only API and SELECT-only SQL; unique-vhost metadata only','pages':{}}
    try:
        for kind,path,actor,key in [
            ('catalog',f'/units/{ctx["unit_id"]}/houses','resident','id'),
            ('active','/me/houses','resident','relation_id'),
            ('pending',f'/admin/resident-bind-requests?tenant_id={ctx["tenant_id"]}&property_id={ctx["property_id"]}','manager','id'),
        ]:
            token=ctx['done']['login-'+actor]['access_token']
            join='&' if '?' in path else '?'
            first=client.data('GET',path+join+'limit=50',token=token,label=kind+'-first')
            assert len(first['items'])==50
            cursor=first['items'][-1][key]
            second=client.data('GET',path+join+f'limit=50&after_id={cursor}',token=token,label=kind+'-second')
            ids1=[row[key] for row in first['items']];ids2=[row[key] for row in second['items']]
            assert ids2 and not set(ids1).intersection(ids2) and min(ids2)>max(ids1)
            result['pages'][kind]={'first_ids':ids1,'after_id':cursor,'second_ids':ids2,'next_cursor':second.get('next_cursor'),'total_seen':len(ids1)+len(ids2)}
            for suffix in ('limit=0','limit=101','after_id=-1'):
                client.call('GET',path+join+suffix,token=token,label=kind+'-'+suffix,expected=(422,))
        assert ctx['target_house_id'] in result['pages']['catalog']['second_ids']
        assert ctx['pending_requests'][-1] in result['pages']['pending']['second_ids']
        client.call('GET',f'/admin/resident-bind-requests?tenant_id={ctx["tenant_id"]}&property_id={ctx["property_id"]}',
                    token=ctx['done']['login-resident']['access_token'],label='resident-cannot-list-admin',expected=(403,))
        other=client.data('GET','/me/houses',token=ctx['done']['login-other']['access_token'],label='other-active-houses')
        assert not other['items'];result['other_user_active_house_count']=0
        engine=create_engine(settings.test_database_url)
        try:
            with engine.connect() as conn:
                conn.execute(text('SET TRANSACTION READ ONLY'))
                result['revision']=conn.execute(text('SELECT version_num FROM alembic_version')).scalar_one();assert result['revision']=='20260919_0009'
                def rows(query):
                    return [dict(row) for row in conn.execute(text(query).bindparams(bindparam('ids',expanding=True)),{'ids':ctx['houses']}).mappings()]
                houses=rows('SELECT id,tenant_id,property_id,unit_id,room_no,status FROM houses WHERE id IN :ids ORDER BY id')
                relations=rows('SELECT id,user_id,house_id,relation_type,status,bind_request_id FROM resident_house_relations WHERE house_id IN :ids ORDER BY id')
                requests=rows('SELECT id,user_id,house_id,relation_type,status,reviewer_id FROM resident_bind_requests WHERE house_id IN :ids ORDER BY id')
                assert len(houses)==51 and len(relations)==50 and len(requests)==101
                assert all(row['user_id']==ctx['identities']['resident'] and row['status']=='ACTIVE' for row in relations)
                assert len([row for row in requests if row['status']=='APPROVED'])==50
                assert [row['id'] for row in requests if row['status']=='PENDING']==ctx['pending_requests']
                result['persisted']={'houses':houses,'relations':relations,'requests':requests}
        finally:engine.dispose()
        queue=subprocess.run(['docker','exec','property-ai-rabbitmq-1','rabbitmqctl','list_queues','-p','property_ai_auth001','name','messages_ready','messages_unacknowledged','consumers'],capture_output=True,text=True,timeout=30)
        assert queue.returncode==0
        result['queue']={'vhost':'property_ai_auth001','actual_output':queue.stdout,'message_content_read':False,'shared_vhost_accessed':False}
        result['requests']=client.requests;result['collected_at_utc']=datetime.now(UTC).isoformat();result['status']='API_DB_PASS_PENDING_INDEPENDENT_REVIEW'
        (OUT/'api-database-queue-evidence.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
        print(json.dumps({'run_id':ctx['run_id'],'status':result['status'],'pages':{k:v['total_seen'] for k,v in result['pages'].items()}}))
    finally:client.close()


if __name__=='__main__':main()
