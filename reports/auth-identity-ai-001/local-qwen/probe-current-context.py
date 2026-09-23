"""SELECT original order/context, then real v4 inference without a business replay."""
from pathlib import Path
from datetime import UTC, datetime, timedelta
from dataclasses import asdict
import hashlib
import json
import runpy

ROOT=Path(r'D:\property-ai')
OUT=Path(__file__).parent
h=runpy.run_path(str(ROOT/'reports/notification-events-inbox-013/run-runtime.py'))
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from app.core.config import Settings
from app.domain.ai import WorkOrderAnalysisOutput, map_analysis_output, WORK_ORDER_ANALYSIS_PROMPT_VERSION
from app.model.work_order import WorkOrder, WorkOrderContent
from app.repository.ai import AiRepository
from app.provider.factory import build_llm_provider
from app.service.analysis import AnalysisService, AnalysisSnapshot

target=OUT/'probe-current-context-v4.json'
assert not target.exists()
ctx=json.loads((ROOT/'tmp/selfhost-ai-local/business-context.json').read_text(encoding='utf-8'))
prior=json.loads((OUT/'business-database-runtime.json').read_text(encoding='utf-8'))
settings=h['_guard']()
engine=create_engine(settings.test_database_url)
try:
    with Session(engine) as session:
        session.execute(text('SET TRANSACTION READ ONLY'))
        record=session.get(WorkOrder,ctx['work_order_id'])
        assert record.tenant_id==ctx['tenant_id'] and record.property_id==ctx['property_id']
        content=session.execute(text('SELECT text,extra_notes,asr_text FROM work_order_contents WHERE work_order_id=:id'),{'id':record.id}).mappings().one()
        assert not content['extra_notes'] and not content['asr_text'], 'Input changed; stop rather than use a partial snapshot'
        original=content['text']
        repo=AiRepository(session)
        context=repo.get_analysis_context(record,since=record.created_at-timedelta(days=settings.ai_context_recent_days),reference_at=record.created_at,limit=settings.ai_context_recent_orders_limit)
        old_hash=hashlib.sha256(json.dumps({'prompt_version':'wo-analysis-v3','work_order_id':record.id,'text':original,'asr':'','media':'','context':context},ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        assert old_hash==prior['analyses'][0]['input_hash'], 'Cannot reconstruct identical v3 input; do not infer equivalence'
        snapshot=AnalysisSnapshot(work_order_id=record.id,tenant_id=record.tenant_id,property_id=record.property_id,house_id=record.house_id,building_id=repo.get_building_id(record.house_id),analysis_id=prior['analyses'][0]['id'],analysis_version=1,work_order_version=record.version,input_hash=old_hash,text=original,existing_asr_text=None,images=(),audio=(),context=context)
    model_settings=Settings(_env_file=None,app_env='test',llm_provider='production',llm_base_url='http://127.0.0.1:11435/v1',llm_model='qwen3.5:2b',llm_api_key='local-loopback-no-auth',llm_disable_thinking=True,llm_max_output_tokens=512,llm_timeout_seconds=60)
    service=AnalysisService.__new__(AnalysisService)
    service._llm=build_llm_provider(model_settings)
    service._clock=lambda:datetime.now(UTC)
    logs=[]
    service._write_call_log=lambda *args,**kwargs:logs.append(kwargs)
    payload,error,status=service._run_llm(snapshot,asr_text=None,vision_text=None,request_id=ctx['run_id']+'-context-probe',attempt=1)
    mapped=map_analysis_output(WorkOrderAnalysisOutput.model_validate(payload)) if payload else None
    e={'task_id':'SELFHOST-AI-LOCAL-001','mode':'SELECT exact existing input + direct real model, no business/queue replay',
       'work_order_id':snapshot.work_order_id,'prompt_version':WORK_ORDER_ANALYSIS_PROMPT_VERSION,'prior_input_hash_verified':old_hash,
       'current_report':original,'context':context,'raw_payload':payload,'mapped':asdict(mapped) if mapped else None,
       'error':error,'call_status':status,'measured_call':logs,'business_state_changed':False,
       'collector_limit':'First SELECT used the wrong column name and failed before inference; corrected to existing text/extra_notes/asr_text, no data mutation.',
       'result':'QUALIFIED_DIRECT_PROBE' if mapped and mapped.category.value=='ELECTRICAL' and mapped.required_skill=='ELECTRICAL' and not mapped.low_confidence else 'NOT_QUALIFIED'}
    target.write_text(json.dumps(e,ensure_ascii=False,indent=2,default=str)+'\n',encoding='utf-8')
    print(json.dumps({k:e[k] for k in ('result','raw_payload','mapped')},ensure_ascii=False,default=str))
finally:
    engine.dispose()
