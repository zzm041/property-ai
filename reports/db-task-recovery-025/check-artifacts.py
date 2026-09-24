"""Local evidence/source integrity only; no API/DB/queue or model calls."""
import ast
import hashlib
import json
from pathlib import Path
import re
import subprocess
root=Path(__file__).resolve().parents[2];folder=Path(__file__).parent
ctx=json.loads((root/'tmp/db-task-recovery-025/context.json').read_text(encoding='utf-8'))
checked=[]
for file in sorted(folder.iterdir()):
    if not file.is_file() or file.suffix=='.png':continue
    value=file.read_text(encoding='utf-8')
    assert not any(phone.removeprefix('+86') in value for phone in ctx['phones'].values()),file.name
    assert not any(token in value for token in ctx['tokens'].values()),file.name
    assert not re.search(r'eyJ[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+',value),file.name
    if file.suffix=='.json':json.loads(value)
    if file.suffix=='.py':ast.parse(value,filename=file.name)
    checked.append(file.name)
assert hashlib.sha256((root/'miniprogram/project.config.json').read_bytes()).hexdigest()=='342ed0b61fe9c74e7a5ce82198c8f36bd9341d5cda83de2c408f33256867fe1f'
m=json.loads((root/'reports/mp-current-full-regression/coverage-matrix.json').read_text(encoding='utf-8'))
old=json.loads(subprocess.check_output(['git','show','ada92e1:reports/mp-current-full-regression/coverage-matrix.json'],cwd=root,text=True,encoding='utf-8'))
assert m['items']==old['items'] and m['extra_v1_from_prd_acceptance']==old['extra_v1_from_prd_acceptance']
assert len(m['items'])==len(m['current_effective_view']['rows'])==83
assert {r['id'] for r in m['items']}=={r['requirement_id'] for r in m['current_effective_view']['rows']}
print(json.dumps({'artifact_text_files_validated':len(checked),'new_run_credentials_absent':True,'protected_config_unchanged':True,'original83_and_extra_history_unchanged':True,'current_unique_ids':83,'business_operations':0}))
