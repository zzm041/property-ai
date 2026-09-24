"""One newly authorized run, reuse bounded collector; never resume old B budget."""
import argparse
from datetime import UTC,datetime
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
ROOT=Path(__file__).resolve().parents[3]
BASE=Path(__file__).parent
OUT=BASE/'crossfield-diagnostic-01'
PRIVATE=ROOT/'tmp/selfhost-ai-local/crossfield-diagnostic-01'
PILLOW=ROOT/'tmp/selfhost-ai-local/image-tools/site-packages'
sys.path.insert(0,str(PILLOW))
spec=importlib.util.spec_from_file_location('bounded_collector',BASE/'run-approved-supplement.py')
h=importlib.util.module_from_spec(spec);spec.loader.exec_module(h)
h.OUT=OUT;h.EXPECTED_PROMPT_VERSION='wo-analysis-v9-duty-contract'
h.__file__=__file__  # Child launches this exact profile, not the old run.
original_plans=h.plans
def plans():
    result=original_plans();early=['GOLD-005','GOLD-006','GOLD-007','GOLD-009','GOLD-010','GOLD-011']
    ordered=[next(r for r in result if r['case']['id']==name) for name in early]
    ordered += [r for r in result if r['case']['id'] not in early]
    assert len(ordered)==32 and len({r['case']['id'] for r in ordered})==28
    return ordered
h.plans=plans
def frozen():
    manifest=h.read(OUT/'frozen-inputs-v2.json');root=Path(manifest['private_frozen_root'])
    for item in manifest['files']:
        if h.sha(root/item['path'])!=item['sha256']:raise RuntimeError('Frozen copy changed')
        if h.sha(ROOT/item['path'])!=item['sha256']:raise RuntimeError('Frozen live source/input changed')
    return root,manifest
h.frozen=frozen

def freeze():
    assert not (OUT/'frozen-inputs-v2.json').exists()
    PRIVATE.mkdir(exist_ok=True);target=PRIVATE/'frozen-v2';target.mkdir()
    prior=h.read(BASE/'supplement-024-rubric-01/frozen-inputs.json')
    names=[i['path'] for i in prior['files']]
    names += ['backend/app/domain/audit.py','backend/app/service/auto_dispatch.py',
              'reports/auth-identity-ai-001/local-qwen/run-approved-supplement.py',
              'reports/auth-identity-ai-001/local-qwen/run-crossfield-diagnostic.py']
    entries=[]
    for name in sorted(set(names)):
        source=ROOT/name;dest=target/name;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,dest)
        entries.append({'path':name,'sha256':h.sha(source)})
    OUT.mkdir(exist_ok=True)
    h.append_file(OUT/'frozen-inputs-v2.json',{'base_sha':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        'private_frozen_root':str(target),'files':entries,'protocol':'wo-analysis-v9-duty-contract / category-duty-skill-v1',
        'budget':{'requests':32,'wall_seconds':1800,'per_request_seconds':60,'repairs':0},
        'old_history':'Old28/128/3of3 and B5 untouched; this is separately authorized',
        'rubric':{'facts_risk_category_unchanged':True,'derived_skill_must_follow_existing_catalog':True,
                  'raw_answer_always_preserved':True,'dangerous_hallucination_or_history_pollution':'STOP',
                  'Chinese_summary_and_complete_current_issues_required':True,'photos_major_visible_content_required':True},
        'order_note':'Original known duty conflicts first, then necessary risk/insufficient boundaries; same28 inputs, no replacement'})
    # Rebind metadata-only observed process; no inference/warmup.
    command="Get-CimInstance Win32_Process -Filter 'ProcessId=2152' | Select-Object ProcessId,ExecutablePath,@{N='created';E={$_.CreationDate.ToString('o')}} | ConvertTo-Json -Compress"
    actual=json.loads(subprocess.check_output(['powershell','-NoProfile','-Command',command],text=True))
    old=h.read(BASE/'supplement-024-rubric-01/runtime.json')
    assert datetime.fromisoformat(actual['created'])==datetime.fromisoformat(old['process_created_at']) and actual['ExecutablePath']==old['executable']
    old.update(supplement_id='CROSSFIELD-DIAGNOSTIC-01',verified_at_utc=datetime.now(UTC).isoformat(),inference_calls_this_check=0)
    h.append_file(OUT/'runtime.json',old)
    assert h.prepare_images()
    print(json.dumps({'status':'FROZEN','files':len(entries),'requests':0,'images':len(h.read(OUT/'images.json'))}))
def judge(number,decision,note):
    folder=OUT/f'request-{number:02d}'
    assert folder.exists() and (folder/'response.json').exists()
    response=h.read(folder/'response.json')
    if decision=='accept':assert not response['contract_errors'] and not (OUT/'stop.json').exists()
    mapping=None
    if response['kind']=='LLM':
        from dataclasses import asdict
        from app.domain.ai import WorkOrderAnalysisOutput,map_analysis_output
        mapping=asdict(map_analysis_output(WorkOrderAnalysisOutput.model_validate(response['provider_result'][0])))
    h.append_file(folder/'judgement.json',{'continue':decision=='accept','source':'main implementer inspected original input/output; independent review pending',
        'response_sha256':h.sha(folder/'response.json'),'note':note,'raw_vs_effective':mapping})
    if decision=='stop' and not (OUT/'stop.json').exists():h.append_file(OUT/'stop.json',{'reason':'CRITICAL_SEMANTIC_ERROR','number':number,'status':'NOT_READY','note':note})
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('mode',choices=['freeze','prepare','next','child','judge']);p.add_argument('--number',type=int);p.add_argument('--decision',choices=['accept','stop']);p.add_argument('--note');a=p.parse_args()
    if a.mode=='freeze':freeze()
    elif a.mode=='prepare':h.prepare_images()
    elif a.mode=='next':h.execute_next()
    elif a.mode=='child':h.child(a.number)
    else:judge(a.number,a.decision,a.note)
