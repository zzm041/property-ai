"""019 actual persisted two-image API order/Worker, plus SELECT-only input/log binding."""
from pathlib import Path
from datetime import UTC, datetime, timedelta
import hashlib
import json
import runpy
import struct
import subprocess
import zlib

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).parent
PRIVATE = ROOT / 'tmp/ai-analysis-input-019/context.json'
h = runpy.run_path(str(ROOT / 'reports/notification-events-inbox-013/run-runtime.py'))
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from app.model.work_order import WorkOrder
from app.repository.ai import AiRepository
from app.domain.ai import WORK_ORDER_ANALYSIS_PROMPT_VERSION


def main():
    settings = h['_guard']()
    ctx = json.loads(PRIVATE.read_text(encoding='utf-8'))
    page = json.loads((OUT/'page-evidence.json').read_text(encoding='utf-8'))
    assert 'create' in page['completed_phases']
    target = OUT/'api-database-evidence.json'
    e = json.loads(target.read_text(encoding='utf-8')) if target.exists() else {
        'task_id':'AI-ANALYSIS-INPUT-019','run_id':ctx['run_id'],'product_sha':page['code_sha'],
        'results':{},'requests':[], 'mode':'Real normal page credential reused, normal file/content/repair APIs, actual independentWorker; not UI creation of images and not real-model effects.',
    }
    assert not e.get('status'), 'Completed; no replay'
    client = h['Client'](ctx['run_id'])
    engine = create_engine(settings.test_database_url)
    token = ctx['resident_token']

    def save():
        e['requests'].extend(client.requests)
        client.requests.clear()
        target.write_text(json.dumps(e,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
        PRIVATE.write_text(json.dumps(ctx,ensure_ascii=False,indent=2),encoding='utf-8')

    def step(label, operation):
        if label not in e['results']:
            e['results'][label] = operation()
            save()
        return e['results'][label]

    def png(rgb):
        def chunk(kind, body):
            return struct.pack('>I',len(body))+kind+body+struct.pack('>I',zlib.crc32(kind+body))
        return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',8,8,8,2,0,0,0))+chunk(b'IDAT',zlib.compress((b'\0'+bytes(rgb)*8)*8))+chunk(b'IEND',b'')

    try:
        me = client.data('GET','/me',token=token,label='normal-page-principal')
        assert me['user_id'] == ctx['identities']['resident'] and 'SYSTEM_ADMIN' not in me['roles']
        files = []
        for index, color in enumerate(((30,90,180),(190,70,40)), start=1):
            content = png(color)
            registered = step(f'file-{index}',lambda index=index, content=content: client.data('POST','/files',token=token,label=f'file-{index}',body={
                'tenant_id':ctx['tenant_id'],'property_id':ctx['property_id'],'mime_type':'image/png',
                'size_bytes':len(content),'original_name':ctx['run_id']+f'-{index}.png'}))
            file_id = registered['id']
            if f'upload-{index}' not in e['results']:
                response = client.http.post(f'/files/{file_id}/content',headers={'Authorization':'Bearer '+token,'X-Request-Id':ctx['run_id']+f'-upload-{index}'},files={'file':(f'ai019-{index}.png',content,'image/png')})
                result = {'status':response.status_code,'request_id':response.json().get('request_id'),'file_id':file_id,'bytes':len(content),'sha256':hashlib.sha256(content).hexdigest()}
                e['results'][f'upload-{index}'] = result
                save()
                assert response.status_code == 200
            files.append(file_id)
        created = step('multi-image-created',lambda:client.data('POST','/work-orders',token=token,label='multi-image-created',headers={'Idempotency-Key':ctx['run_id']+'-multi-image'},body={
            'house_id':ctx['house_id'],'text':ctx['run_id']+' 两张合成测试图片，用于验证逐图接线；客厅灯具线路故障。','file_ids':files}))
        ctx['api_order_id'] = created['id']
        save()
        assigned = step('multi-image-auto-assigned',lambda:h['_wait_assigned'](client,token,ctx['api_order_id']))
        assert len(assigned['media']) == 2
        assert assigned['assignments'][-1]['reason'] == 'AUTO_DISPATCH' and assigned['assignments'][-1]['assigned_by_user_id'] is None
        for index, file_id in enumerate(files,start=1):
            response = client.http.get(f'/files/{file_id}/content',headers={'Authorization':'Bearer '+token})
            assert response.status_code == 200
            digest = hashlib.sha256(response.content).hexdigest()
            assert digest == e['results'][f'upload-{index}']['sha256']
            e['results'][f'download-{index}'] = {'status':200,'file_id':file_id,'sha256':digest,'bytes':len(response.content),'mode':'actual authorized content response, not metadata'}
            save()
        with Session(engine) as session:
            session.execute(text('SET TRANSACTION READ ONLY'))
            revision = session.execute(text('SELECT version_num FROM alembic_version')).scalar_one()
            assert revision == '20260919_0009'
            repo = AiRepository(session)
            e['database'] = {'target':'127.0.0.1:3307/property_ai_auth001_test','revision':revision,'orders':[]}
            for order_id in (ctx['page_order_id'],ctx['api_order_id']):
                order = session.get(WorkOrder,order_id)
                assert order.tenant_id == ctx['tenant_id'] and order.property_id == ctx['property_id'] and order.reporter_user_id == ctx['identities']['resident']
                analysis = repo.get_current_analysis(order_id)
                assert analysis.status == 'COMPLETED' and analysis.prompt_version == 'wo-analysis-v2'
                reference = order.created_at
                context = repo.get_analysis_context(order,since=reference-timedelta(days=settings.ai_context_recent_days),reference_at=reference,limit=settings.ai_context_recent_orders_limit)
                assert context['house']['room_no'] and context['reporter_relationships'] == ['OWNER'] and len(context['recent_house_orders']) <= settings.ai_context_recent_orders_limit
                content = session.execute(text('SELECT text,asr_text,extra_notes FROM work_order_contents WHERE work_order_id=:id'),{'id':order_id}).mappings().one()
                inputs = repo.list_media_with_files(order_id)
                images = [(m,f) for m,f in inputs if m.kind=='IMAGE' and m.taken_phase=='REPORT']
                audio = [(m,f) for m,f in inputs if m.kind=='AUDIO' and m.taken_phase=='REPORT']
                assert not audio and not content['extra_notes']
                media_identity = '|'.join(f'{m.kind}:{f.mime_type}:{f.storage_key}' for m,f in images)
                expected = hashlib.sha256(json.dumps({'prompt_version':WORK_ORDER_ANALYSIS_PROMPT_VERSION,'work_order_id':order_id,'text':content['text'],'asr':content['asr_text'] or '', 'media':media_identity,'context':context},ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
                assert analysis.input_hash == expected, 'Saved Worker input hash differs from current scoped snapshot; inspect, never overwrite'
                logs = [dict(r) for r in session.execute(text('SELECT id,provider_type,provider_mode,operation,status,prompt_version,model_version,token_usage,input_hash,request_id FROM ai_call_logs WHERE work_order_id=:id ORDER BY id'),{'id':order_id}).mappings()]
                assert len(logs) == 1+len(images) and all(r['provider_mode']=='DEVELOPMENT' and r['token_usage'] is None and r['status']=='SUCCESS' for r in logs)
                vision_logs = [r for r in logs if r['provider_type']=='VLM']
                bindings = []
                for (m,f),log in zip(images,vision_logs,strict=True):
                    image_hash = hashlib.sha256(f'{analysis.input_hash}|{m.kind}|{f.mime_type}|{f.storage_key}'.encode()).hexdigest()
                    assert log['input_hash'] == image_hash
                    bindings.append({'media_id':m.id,'file_id':f.id,'ai_call_id':log['id'],'input_hash':image_hash})
                e['database']['orders'].append({'order_id':order_id,'state':order.status,'analysis_id':analysis.id,'prompt_version':analysis.prompt_version,
                    'input_hash':analysis.input_hash,'context_reconstruction':context,'context_boundary':'SELECT-only post-call reconstruction matching saved hash, not a real-model comprehension claim',
                    'media_calls':bindings,'ai_call_logs':logs})
        q = subprocess.run(['docker','exec','property-ai-rabbitmq-1','rabbitmqctl','list_queues','-p','property_ai_auth001','name','messages_ready','messages_unacknowledged','consumers'],capture_output=True,text=True,timeout=30)
        assert q.returncode == 0
        e['queue'] = {'vhost':'property_ai_auth001','actual_output':q.stdout,'shared_vhost_accessed':False,'message_content_read':False}
        e['observed_at_utc'] = datetime.now(UTC).isoformat()
        e['status'] = 'API_DB_WORKER_DEVELOPMENT_PASS_PENDING_INDEPENDENT_REVIEW'
        e['limits'] = ['Two PNGs are actual synthetic stored bytes, not realistic visual quality tests. DevelopmentVLM is fixed; no Qwen invoked.',
                       'Provider-reported non-null token usage is verified by MockTransport unit tests only. LiveDevelopment usage staysNULL.',
                       'Current actualcontext is tenant1/property1/house1/OWNER with bounded older summaries; foreign-scope isolation is source/unit evidence, not a new cross-tenant runtime fixture.',
                       'New two orders remainASSIGNED for this evidence; no SQL status writes or lifecycle replay.']
        save()
        print(json.dumps({'run_id':ctx['run_id'],'page_order':ctx['page_order_id'],'api_order':ctx['api_order_id'],'files':files,'status':e['status']}))
    finally:
        save()
        client.close()
        engine.dispose()


if __name__ == '__main__':
    main()
