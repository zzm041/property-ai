from pathlib import Path
import site
import subprocess
import sys
from uuid import uuid4
ROOT=Path(__file__).resolve().parents[3]
OUT=Path(__file__).parent/'crossfield-diagnostic-01'
if '--child' in sys.argv:
    site.addsitedir(str(ROOT/'.venv/Lib/site-packages'));sys.path.insert(0,str(ROOT/'backend'))
    import pytest
    names=['test_analysis_crossfield_contract.py','test_ai_policy.py','test_ai_analysis_input.py','test_ai_evidence_provenance.py','test_auto_dispatch.py','test_maintenance_dispatch_024.py']
    raise SystemExit(pytest.main(['-q','-p','no:cacheprovider','--basetemp',str(ROOT/'tmp'/('crossfield-unit-'+uuid4().hex))]+['backend/tests/unit/'+n for n in names]))
OUT.mkdir(exist_ok=True)
result=subprocess.run([sys.executable,'-I','-X','utf8',__file__,'--child'],cwd=ROOT,capture_output=True,text=True,encoding='utf-8')
with (OUT/('tests-'+sys.argv[1]+'.txt')).open('x',encoding='utf-8') as f:f.write(result.stdout+result.stderr)
print(result.stdout);raise SystemExit(result.returncode)
