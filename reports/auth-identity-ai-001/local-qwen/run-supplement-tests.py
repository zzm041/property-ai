"""Capture only the new approved collector guard tests; no inference."""
from pathlib import Path
import site
import subprocess
import sys
from uuid import uuid4
ROOT=Path(__file__).resolve().parents[3]
site.addsitedir(str(ROOT/'.venv/Lib/site-packages'))
sys.path.insert(0,str(ROOT/'backend'))
if '--child' in sys.argv:
    import pytest
    raise SystemExit(pytest.main(['-q','-p','no:cacheprovider','--basetemp',str(ROOT/'tmp'/('supplement-tests-'+uuid4().hex)),str(Path(__file__).with_name('test_approved_supplement.py'))]))
label = sys.argv[1] if len(sys.argv) > 1 else 'initial'
target=Path(__file__).parent/('supplement-024-rubric-01/collector-tests-' + label + '.txt')
assert not target.exists()
result=subprocess.run([sys.executable,'-I','-X','utf8',__file__,'--child'],capture_output=True,text=True,encoding='utf-8',cwd=ROOT)
with target.open('x',encoding='utf-8') as stream:stream.write(result.stdout+result.stderr)
print(result.stdout)
raise SystemExit(result.returncode)
