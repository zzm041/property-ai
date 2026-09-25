"""R3's single authorized diagnostic, unchanged 28 inputs, append-only budget."""
import argparse
from datetime import UTC, datetime
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[2]
BASE=ROOT/'reports/auth-identity-ai-001/local-qwen'
OUT=Path(__file__).parent/'diagnostic'
PRIVATE=ROOT/'tmp/real-ai-integration-027/diagnostic'
spec=importlib.util.spec_from_file_location('bounded_collector',BASE/'run-approved-supplement.py')
h=importlib.util.module_from_spec(spec);spec.loader.exec_module(h)
h.OUT=OUT;h.EXPECTED_PROMPT_VERSION='wo-analysis-v10-observed-event';h.__file__=__file__
old_plans=h.plans;old_contract=h.raw_contract

def frozen():
    manifest=h.read(OUT/'frozen.json');root=Path(manifest['private_frozen_root'])
    for item in manifest['files']:
        assert h.sha(root/item['path'])==item['sha256']
        assert h.sha(ROOT/item['path'])==item['sha256'],'Candidate changed during diagnostic'
    return root,manifest
h.frozen=frozen

def plans():
    result=old_plans();early=['GOLD-006','GOLD-005','GOLD-007','GOLD-009','GOLD-010','GOLD-011']
    return [next(r for r in result if r['case']['id']==i) for i in early]+[r for r in result if r['case']['id'] not in early]
h.plans=plans

def images():
    for item in h.read(OUT/'images.json'):
        assert h.sha(Path(item['copy']))==item['copy_sha256']
    return True
h.prepare_images=images

def contract(raw,kind):
    if kind=='VLM':return old_contract(raw,kind)
    from app.domain.observed_event import ObservedAnalysisOutput
    try:
        response=json.loads(raw);choice=response['choices'][0]
        value=json.loads(choice['message']['content'])
        ObservedAnalysisOutput.model_validate(value)
        # Existing strict enum/risk/string/Chinese checks remain, not a relaxed gate.
        legacy={k:v for k,v in value.items() if k!='observations'};legacy['subcategory']='validation-only'
        choice['message']['content']=json.dumps(legacy,ensure_ascii=False)
        return old_contract(json.dumps(response),kind)
    except (ValueError,KeyError,TypeError,IndexError):return ['OBSERVATION_PROTOCOL_INVALID']
h.raw_contract=contract

def freeze():
    assert not OUT.exists(),'Existing run must be resumed, never reset'
    PRIVATE.mkdir(parents=True,exist_ok=True);target=PRIVATE/'frozen';target.mkdir()
    names=[i['path'] for i in h.read(BASE/'crossfield-diagnostic-01/frozen-inputs-v2.json')['files']]
    names+=['backend/app/domain/observed_event.py','reports/real-ai-integration-027/diagnostic.py','docs/05-AI-DISPATCH-SPEC.md']
    entries=[]
    for name in sorted(set(names)):
        dest=target/name;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(ROOT/name,dest)
        entries.append({'path':name,'sha256':h.sha(dest)})
    # Exact process ownership recheck; metadata only, no model request.
    cmd="Get-CimInstance Win32_Process -Filter 'ProcessId=2152' | Select-Object ProcessId,ExecutablePath,@{N='created';E={$_.CreationDate.ToString('o')}} | ConvertTo-Json -Compress"
    actual=json.loads(subprocess.check_output(['powershell','-NoProfile','-Command',cmd],text=True))
    runtime=h.read(BASE/'crossfield-diagnostic-01/runtime.json')
    assert actual['ExecutablePath']==runtime['executable'] and datetime.fromisoformat(actual['created'])==datetime.fromisoformat(runtime['process_created_at'])
    OUT.mkdir()
    h.append_file(OUT/'frozen.json',{'base_sha':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        'private_frozen_root':str(target),'files':entries,'protocol':'observed-event-v1',
        'budget':{'requests':32,'seconds':1800,'per_request_seconds':60},
        'rubric':'Original facts/risk/Chinese/complete issues/images remain; current observed evidence instead of free motive subcategory; ordinary maintenance unified; raw outputs immutable',
        'prior_rounds':'Historical 3/3 untouched; this is one newly authorized R3 run'})
    runtime.update(verified_at_utc=datetime.now(UTC).isoformat(),inference_calls_this_check=0)
    h.append_file(OUT/'runtime.json',runtime)
    h.append_file(OUT/'images.json',h.read(BASE/'crossfield-diagnostic-01/images.json'));images()
    print('FROZEN; zero inference requests')

def judge(number,decision,note):
    from app.domain.observed_event import ObservedAnalysisOutput,bind_observations
    from app.domain.ai import WorkOrderAnalysisOutput,map_analysis_output
    from dataclasses import asdict
    folder=OUT/f'request-{number:02d}';r=h.read(folder/'response.json');mapping=None
    if decision=='accept':
        assert not r['contract_errors'] and not (OUT/'stop.json').exists()
        if r['kind']=='LLM':
            value=r['provider_result'][0];sources={'report':r['case'].get('report',r['case'].get('paired_report')),'asr':'','vision':''}
            if r['case']['id'].startswith('PHOTO'):sources['vision']=h.read(OUT/f'request-{number-1:02d}/response.json')['provider_result'][0]
            observed=ObservedAnalysisOutput.model_validate(value);key,trace=bind_observations(observed,sources)
            mapping={'trace':trace,'mapped':asdict(map_analysis_output(WorkOrderAnalysisOutput.model_validate({**observed.model_dump(exclude={'observations'}),'subcategory':key})))}
    h.append_file(folder/'judgement.json',{'continue':decision=='accept','source':'main inspected original input/output; independent review pending',
        'response_sha256':h.sha(folder/'response.json'),'note':note,'raw_vs_effective':mapping})
    if decision=='stop' and not (OUT/'stop.json').exists():h.append_file(OUT/'stop.json',{'reason':'CRITICAL_SEMANTIC_OR_EVIDENCE_ERROR','number':number,'status':'NOT_READY','note':note})

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('mode',choices=['freeze','next','child','judge']);p.add_argument('--number',type=int);p.add_argument('--decision',choices=['accept','stop']);p.add_argument('--note');a=p.parse_args()
    if a.mode=='freeze':freeze()
    elif a.mode=='next':h.execute_next()
    elif a.mode=='child':h.child(a.number)
    else:judge(a.number,a.decision,a.note)
