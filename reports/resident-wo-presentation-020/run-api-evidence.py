"""Normal authentication; GET and SQL SELECT only for current 020 objects."""
from pathlib import Path
from datetime import UTC, datetime
from zoneinfo import ZoneInfo
import json
import runpy
import subprocess

ROOT=Path(__file__).resolve().parents[2]
OUT=Path(__file__).parent
PRIVATE=ROOT/'tmp/resident-wo-presentation-020/context.json'
h=runpy.run_path(str(ROOT/'reports/notification-events-inbox-013/run-runtime.py'))
from sqlalchemy import create_engine, text


def main():
    settings=h['_guard']()
    ctx=json.loads(PRIVATE.read_text(encoding='utf-8'))
    page=json.loads((OUT/'page-evidence.json').read_text(encoding='utf-8'))
    before=json.loads((OUT/'preflight.json').read_text(encoding='utf-8'))
    assert page['complete'] and page['code_sha']=='b75fa97112b3144f9d4702e4782b231bb775c19c'
    target=OUT/'api-database-evidence.json'
    assert not target.exists(), 'Keep saved run; inspect checkpoint instead of replay'
    client=h['Client'](ctx['run_id'])
    engine=create_engine(settings.test_database_url)
    e={'task_id':'RESIDENT-WO-PRESENTATION-020','run_id':ctx['run_id'],'product_sha':page['code_sha'],
       'mode':'Actual normal page-issued session, GET own objects; other normalSMS principal; SELECT-only DB; no lifecycle mutations','orders':[]}
    try:
        resident=ctx['resident_token']
        me=client.data('GET','/me',token=resident,label='resident-principal')
        assert me['user_id']==ctx['identities']['resident'] and 'SYSTEM_ADMIN' not in me['roles']
        with engine.connect() as c:
            c.execute(text('SET TRANSACTION READ ONLY'))
            e['revision']=c.execute(text('SELECT version_num FROM alembic_version')).scalar_one()
            assert e['revision']=='20260919_0009'
            params={'history':ctx['history_order_id'],'active':ctx['active_order_id']}
            e['business_counts']={table:c.execute(text(f'SELECT COUNT(*) FROM {table} WHERE work_order_id IN (:history,:active)'),params).scalar_one() for table in before['business_counts']}
            assert e['business_counts']==before['business_counts'], 'Read-only page must not change lifecycle rows'
            for source in before['orders']:
                order_id=source['id']
                value=client.data('GET',f'/work-orders/{order_id}',token=resident,label=f'own-{order_id}')
                assert value['work_order']['status']==source['status'] and value['work_order']['version']==source['version']
                sqlparams={'id':order_id,'tenant':ctx['tenant_id'],'property':ctx['property_id']}
                row=dict(c.execute(text('SELECT id,status,version,created_at,updated_at FROM work_orders WHERE id=:id AND tenant_id=:tenant AND property_id=:property'),sqlparams).mappings().one())
                timezone=c.execute(text('SELECT timezone FROM properties WHERE id=:property AND tenant_id=:tenant'),sqlparams).scalar_one()
                names=dict(c.execute(text('SELECT e.id,u.display_name FROM employees e JOIN users u ON u.id=e.user_id WHERE e.tenant_id=:tenant AND e.id IN (SELECT employee_id FROM work_order_assignments WHERE work_order_id=:id AND tenant_id=:tenant AND property_id=:property UNION SELECT assignee_id FROM work_order_processes WHERE work_order_id=:id AND tenant_id=:tenant AND property_id=:property)'),sqlparams).all())
                expected_names={str(k):v for k,v in names.items() if v and v.strip()}
                assert value['display']['assignee_names']==expected_names and value['display']['property_timezone']==timezone
                created=datetime.fromisoformat(value['work_order']['created_at'].replace('Z','+00:00'))
                assert created==row['created_at'].replace(tzinfo=UTC)
                for raw,label in value['display']['timestamp_labels'].items():
                    local=datetime.fromisoformat(raw.replace('Z','+00:00')).astimezone(ZoneInfo(timezone))
                    offset=local.strftime('%z')
                    assert label==local.strftime('%Y-%m-%d %H:%M:%S')+f' UTC{offset[:3]}:{offset[3:]}'
                processes=[dict(r) for r in c.execute(text('SELECT id,cycle_no,assignee_id,started_at,ended_at FROM work_order_processes WHERE work_order_id=:id ORDER BY cycle_no'),sqlparams).mappings()]
                receipts=[dict(r) for r in c.execute(text('SELECT id,process_id,finished_at FROM work_order_receipts WHERE work_order_id=:id ORDER BY id'),sqlparams).mappings()]
                assert [p['id'] for p in value['processes']]==[p['id'] for p in processes]
                assert {r['id']:r['process_id'] for r in value['receipts']}=={r['id']:r['process_id'] for r in receipts}
                for receipt in receipts:
                    actual=next(r for r in value['receipts'] if r['id']==receipt['id'])
                    assert datetime.fromisoformat(actual['finished_at'].replace('Z','+00:00'))==receipt['finished_at'].replace(tzinfo=UTC)
                e['orders'].append({'detail':value,'database':{'work_order':row,'processes':processes,'receipts':receipts,'current_assignee_names':names,'timezone':timezone}})
        # Authentication requests are not claimed as a business-page action.
        if not ctx.get('other_token'):
            auth=client.sms_login(ctx['phones']['other'],'other-normal')
            ctx['other_token']=auth['access_token']
            PRIVATE.write_text(json.dumps(ctx,ensure_ascii=False,indent=2),encoding='utf-8')
        other=client.data('GET','/me',token=ctx['other_token'],label='other-principal')
        assert other['user_id']==ctx['identities']['other']
        e['negative']={}
        for order_id in (ctx['history_order_id'],ctx['active_order_id']):
            status,payload=client.call('GET',f'/work-orders/{order_id}',token=ctx['other_token'],label=f'cross-user-{order_id}',expected=(404,))
            assert 'data' not in payload
            e['negative'][str(order_id)]={'status':status,'error_code':payload['error']['code'],'metadata_leaked':False}
        status,payload=client.call('GET',f'/work-orders/{ctx["history_order_id"]}',label='anonymous',expected=(401,))
        e['anonymous']={'status':status,'error_code':payload['error']['code']}
        q=subprocess.run(['docker','exec','property-ai-rabbitmq-1','rabbitmqctl','list_queues','-p','property_ai_auth001','name','messages_ready','messages_unacknowledged','consumers'],capture_output=True,text=True,timeout=30)
        assert q.returncode==0
        e['queue']={'vhost':'property_ai_auth001','actual_output':q.stdout,'shared_access':False,'message_content_read':False}
        e['status']='API_DB_READONLY_PASS_PENDING_INDEPENDENT_REVIEW'
        e['observed_at_utc']=datetime.now(UTC).isoformat()
        e['limits']=['Actualruntime timezone Asia/Shanghai;DST/unknown-timezone cases are unit simulations.',
                     'Actual cross-user same-tenant404 and anonymous401; foreign-scope query filters are source/unit evidence, no new foreign-tenant fixture.',
                     'Current display names are not historical name snapshots. List is explicitlyUTC, detail isPropertytimezone.',
                     'No new workorder/assignment/process/receipt/log/feedback created; normalSMS login adds its own auth/audit/session records.']
        print(json.dumps({'run_id':ctx['run_id'],'orders':[x['detail']['work_order']['id'] for x in e['orders']],'status':e['status']}))
    except Exception as error:
        e['error']=type(error).__name__+': '+str(error)
        raise
    finally:
        e['requests']=client.requests
        # Values are synthetic; auth tokens/phones never enter this public object.
        target.write_text(json.dumps(e,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
        client.close()
        engine.dispose()


if __name__=='__main__':
    main()
