from pathlib import Path
import site
import subprocess
import sys
from uuid import uuid4
ROOT=Path(__file__).resolve().parents[3]
if '--child' in sys.argv:
    site.addsitedir(str(ROOT/'.venv/Lib/site-packages'));sys.path.insert(0,str(ROOT/'backend'))
    import pytest
    raise SystemExit(pytest.main(['-q','-p','no:cacheprovider','--basetemp',str(ROOT/'tmp'/('crossfield-collector-'+uuid4().hex)),str(Path(__file__).with_name('test_crossfield_collector.py'))]))
result=subprocess.run([sys.executable,'-I','-X','utf8',__file__,'--child'],cwd=ROOT,capture_output=True,text=True,encoding='utf-8')
with (Path(__file__).parent/'crossfield-diagnostic-01/collector-tests.txt').open('x',encoding='utf-8') as f:f.write(result.stdout+result.stderr)
print(result.stdout);raise SystemExit(result.returncode)
