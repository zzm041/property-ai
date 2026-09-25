"""Seal existing synthetic records only; never invokes business or inference."""
from pathlib import Path
import hashlib
import json
import shutil
import subprocess

ROOT=Path(__file__).resolve().parents[2]
OUT=Path(__file__).resolve().parent
PRIVATE=ROOT/'tmp/real-ai-integration-027/runtime'
def read(p):return json.loads(p.read_text(encoding='utf-8'))
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,v):
    with p.open('x',encoding='utf-8') as f:json.dump(v,f,ensure_ascii=False,indent=2)

ctx=read(PRIVATE/'context.json')
secrets=[*ctx['phones'].values(),*ctx['tokens'].values(),ctx['internal_key']]
public=OUT/'model-calls';public.mkdir(exist_ok=True)
for reservation in sorted((PRIVATE/'model-calls').glob('call-*/reservation.json')):
    target=public/reservation.parent.name;target.mkdir(exist_ok=True)
    for name in ('reservation.json','result.json','stdout.jsonl','stderr.txt','input.json'):
        if name=='input.json' and read(reservation)['kind']=='asr':continue
        source=reservation.parent/name
        if not source.exists():continue
        raw=source.read_text(encoding='utf-8')
        assert not any(secret and secret in raw for secret in secrets),'Private value: refuse publication'
        dest=target/name
        if dest.exists():assert sha(dest)==sha(source)
        else:shutil.copy2(source,dest)
    if read(reservation)['kind']=='asr':
        save(target/'input-reference.json',{'reference':'audio-reference.json','input_sha256':read(reservation)['input_hash'],'reason':'Existing synthetic PCM retained privately; no duplicated base64 audio'})

paths=[
 'backend/app/core/config.py','backend/app/domain/ai.py','backend/app/domain/audit.py','backend/app/domain/knowledge.py','backend/app/domain/observed_event.py',
 'backend/app/provider/llm/__init__.py','backend/app/provider/vector/__init__.py','backend/app/service/analysis.py','backend/app/service/auto_dispatch.py','backend/app/service/retrieval.py',
 'backend/tests/unit/test_ai_analysis_input.py','backend/tests/unit/test_observed_event_027.py','backend/tests/unit/test_local_ai_http_027.py',
 'scripts/ops/local_ai_http.py','scripts/ops/local_ai_models.py','docs/05-AI-DISPATCH-SPEC.md',
 'miniprogram/pages/home/home.js','miniprogram/pages/home/home.wxml']
save(OUT/'review-source-manifest.json',{'base_sha':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
 'files':[{'path':p,'sha256':sha(ROOT/p)} for p in paths],
 'runtime_backend_sha256':read(OUT/'candidate.json')['source_sha256'],
 'model_adapter_receipt':'model-adapter-fix1.json','protected_project_config_sha256':sha(ROOT/'miniprogram/project.config.json')})
print('Sealed existing synthetic inputs/results and exact review source manifest; zero inference')
