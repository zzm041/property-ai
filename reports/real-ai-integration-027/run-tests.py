"""Only new R3 and directly affected analysis/incident/proxy contracts; no DB fixtures."""
from pathlib import Path
import site
import subprocess
import sys
from datetime import UTC,datetime
ROOT=Path(__file__).resolve().parents[2]
if '--child' in sys.argv:
    site.addsitedir(str(ROOT/'.venv/Lib/site-packages'));sys.path.insert(0,str(ROOT/'backend'))
    import pytest
    files=(['test_local_ai_http_027.py','test_day2_providers.py','test_provider_loopback_proxy.py','test_knowledge_retrieval_scope.py'] if '--r2' in sys.argv else
        ['test_observed_event_027.py','test_analysis_crossfield_contract.py','test_ai_analysis_input.py'])
    if '--adapter-only' in sys.argv:files=['test_local_ai_http_027.py']
    if '--process-only' in sys.argv:files=['test_local_ai_process_027.py']
    selection=['-k','unknown_or_legacy or resource_stop or append_only_budget or total_window'] if '--lifecycle-only' in sys.argv else []
    if '--lifecycle-only' in sys.argv:files=['test_local_ai_http_027.py']
    raise SystemExit(pytest.main(['-q','-p','no:cacheprovider','--basetemp',str(ROOT/'tmp/real-ai-integration-027'/datetime.now(UTC).strftime('unit-%H%M%S')),
        *['backend/tests/unit/'+name for name in files],*selection]))
result=subprocess.run([sys.executable,'-I','-X','utf8',__file__,'--child',*sys.argv[1:]],cwd=ROOT,capture_output=True,text=True,encoding='utf-8')
path=Path(__file__).parent/(datetime.now(UTC).strftime('tests-%Y%m%dT%H%M%S')+'.txt')
with path.open('x',encoding='utf-8') as f:f.write(result.stdout+result.stderr)
print(result.stdout);raise SystemExit(result.returncode)
