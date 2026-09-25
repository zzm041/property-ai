"""R2 API negatives/read-only persistence and one approved real ASR request."""
import argparse
from dataclasses import asdict
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import time

spec=importlib.util.spec_from_file_location('r027',Path(__file__).with_name('runtime.py'))
r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r)
import httpx
from sqlalchemy import text

def rows(engine,sql,params=None):
    with engine.connect() as c:
        c.execute(text('SET TRANSACTION READ ONLY'))
        return [dict(x) for x in c.execute(text(sql),params or {}).mappings()]
def client(actor='resident'):
    c=r.ctx();h=httpx.Client(base_url='http://127.0.0.1:8083/api/v1',headers={'Authorization':'Bearer '+c['tokens'][actor]},timeout=120,trust_env=False)
    me=h.get('/me');assert me.status_code==200 and me.json()['data']['user_id']==c['identities'][actor],'Use a currently logged-in normal page session'
    return h
def snapshot(name):
    s=r.settings();r.guard(s);e=r.create_database_engine(s.active_database_url);c=r.ctx();ids=c['documents'];assert 0<len(ids)<=3
    joined=','.join(map(str,ids));result={'utc':r.stamp(),'run_id':c['run_id'],'documents':rows(e,'SELECT id,tenant_id,property_id,title,visibility,status,current_version FROM knowledge_documents WHERE id IN ('+joined+')'),
        'versions':rows(e,'SELECT id,document_id,version,status,content_hash,source_text FROM knowledge_document_versions WHERE document_id IN ('+joined+')'),
        'jobs':rows(e,'SELECT id,document_id,version,status,attempt,error_code,started_at,finished_at FROM knowledge_ingestion_jobs WHERE document_id IN ('+joined+')'),
        'chunks':rows(e,'SELECT id,tenant_id,property_id,document_id,version,visibility,source_locator,excerpt,vector_id FROM knowledge_chunks WHERE document_id IN ('+joined+')'),
        'intents':rows(e,"SELECT i.id,i.operation,i.resource_id,i.status,i.execution_attempt,i.publish_attempt,i.task_id,i.last_error_code FROM task_intents i JOIN knowledge_ingestion_jobs j ON i.operation='INGEST_KNOWLEDGE' AND i.resource_id=j.id WHERE j.document_id IN ("+joined+')')}
    gold=r.read(r.OUT/'rag-cases.json');bykey={d['key']:d for d in gold['documents']};byid={v:k for k,v in c['document_keys'].items()}
    for v in result['versions']:
        expected=bykey[byid[v['document_id']]]['text' if v['version']==1 else 'v2']
        assert v['source_text']==expected,'Actual page content differs from fixed gold; retain, do not silently substitute'
    from app.provider.vector import MilvusKnowledgeStore
    store=MilvusKnowledgeStore(s);collection=store.ensure_collection(1024)
    result['milvus']=collection.query(expr='document_id in ['+joined+']',output_fields=['chunk_id','tenant_id','property_id','document_id','document_version','visibility','vector'])
    # Preserve numeric evidence without committing full embeddings repeatedly.
    for item in result['milvus']:
        values=item.pop('vector');result_bytes=json.dumps([float(x) for x in values]).encode()
        item.update(dimensions=len(values),l2_norm=sum(float(x)**2 for x in values)**.5,vector_sha256=hashlib.sha256(result_bytes).hexdigest())
    result['model_calls']=[]
    for f in sorted((r.PRIVATE/'model-calls').glob('call-*/reservation.json')):
        reservation=r.read(f);done=r.read(f.parent/'result.json') if (f.parent/'result.json').exists() else {}
        result['model_calls'].append({'number':f.parent.name,**reservation,'status':done.get('status','UNKNOWN'),'total_seconds':done.get('total_seconds'),
            'revision':done.get('revision'),'load_seconds':next((x.get('load_seconds') for x in done.get('events',[]) if x.get('phase')=='inference'),None),
            'inference_seconds':next((x.get('inference_seconds') for x in done.get('events',[]) if 'result'in x),None)})
    r.write(name+'.json',result);e.dispose();print(json.dumps({'saved':name,'docs':ids,'versions':len(result['versions']),'calls':len(result['model_calls'])}))

def negative():
    c=r.ctx();s=r.settings();r.guard(s);e=r.create_database_engine(s.active_database_url)
    foreign=rows(e,'SELECT id,tenant_id FROM properties WHERE id<>:id ORDER BY (tenant_id<>:tenant) DESC,id LIMIT 1',{'id':c['property_id'],'tenant':c['tenant_id']})
    assert foreign;e.dispose();h=client();responses=[]
    cases=[('N01','GET',f"/admin/knowledge-documents/{c['document_keys']['B']}",None,{403,404}),
        ('N02','POST','/admin/knowledge-documents',{'property_id':c['property_id'],'tenant_id':c['tenant_id'],'title':c['run_id']+' denied','document_type':'FAQ','visibility':'RESIDENT','source_text':'仅验证拒绝，不得建立。'},{403}),
        ('N03','POST','/assistant/query',{'property_id':foreign[0]['id'],'question':'请提供该小区的内部资料'},{403}),
        ('N04','POST','/assistant/query',{'property_id':c['property_id'],'tenant_id':foreign[0]['tenant_id'] if foreign[0]['tenant_id']!=c['tenant_id'] else c['tenant_id']+999,'question':'领取便民工具在哪里'},{422})]
    before=len(list((r.PRIVATE/'model-calls').glob('call-*/reservation.json')))
    for label,method,path,body,expected in cases:
        response=h.request(method,path,json=body,headers={'X-Request-Id':c['run_id']+'-'+label})
        data=response.json();responses.append({'case':label,'path':path,'method':method,'status':response.status_code,'response':data})
        assert response.status_code in expected,(label,response.status_code)
    after=len(list((r.PRIVATE/'model-calls').glob('call-*/reservation.json')));assert before==after
    r.write('api-negative.json',{'run_id':c['run_id'],'cases':responses,'inference_delta':0,'source':'actual authenticated API; not UI','foreign_property':foreign[0]});h.close();print('Four real API permission negatives; zero inference')

def asr():
    c=r.ctx();assert not (r.OUT/'asr-http.json').exists(),'Do not repeat completed ASR'
    assert not c.get('asr_http_started'),'Prior ASR completion unknown: inspect without replay'
    s=r.settings();r.guard(s);reference=r.read(r.OUT/'audio-reference.json');audio=Path(reference['path']).read_bytes()
    assert hashlib.sha256(audio).hexdigest().upper()==reference['sha256']
    h=client()
    if 'audio_file' not in c:
        assert not c.get('audio_registration_started'),'Prior registration unknown: inspect instead of duplicating'
        c['audio_registration_started']=r.stamp();r.save_ctx(c)
        response=h.post('/files',json={'mime_type':'audio/wav','size_bytes':len(audio),'original_name':c['run_id']+'.wav','tenant_id':c['tenant_id'],'property_id':c['property_id']},headers={'X-Request-Id':c['run_id']+'-audio-register'})
        assert response.status_code==200,response.status_code
        c['audio_file']=response.json()['data'];r.save_ctx(c)
    media=c['audio_file'];response=h.post(f"/files/{media['id']}/content",files={'file':(c['run_id']+'.wav',audio,'audio/wav')},headers={'X-Request-Id':c['run_id']+'-audio-upload'})
    assert response.status_code==200
    stored=h.get(f"/files/{media['id']}/content");assert stored.status_code==200 and stored.content==audio
    from app.provider.factory import build_asr_provider
    c['asr_http_started']=r.stamp();r.save_ctx(c)
    start=time.monotonic();result=build_asr_provider(s).transcribe(storage_key=media['storage_key'],mime_type='audio/wav')
    r.write('asr-http.json',{'run_id':c['run_id'],'file_id':media['id'],'storage_key':media['storage_key'],'sha256':reference['sha256'],'reference':reference['reference'],
        'actual':asdict(result),'seconds':time.monotonic()-start,'upload_and_authorized_download_equal':True,'mode':'normal API media upload + real HTTP ASR; not native recording',
        'work_order_id':None,'work_order_association':'BLOCKED_BY_R3_NOT_READY; no new orders created'})
    h.close();print(json.dumps({'ASR':asdict(result),'file_id':media['id']},ensure_ascii=False))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('mode',choices=['snapshot','negative','asr']);p.add_argument('--name',default='runtime-final');a=p.parse_args()
    {'snapshot':lambda:snapshot(a.name),'negative':negative,'asr':asr}[a.mode]()
