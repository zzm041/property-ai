"""No inference/network/DB: bounded owned dummy processes only."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import time

import pytest

ROOT=Path(__file__).resolve().parents[3]
spec=importlib.util.spec_from_file_location('local_ai_process_027',ROOT/'scripts/ops/local_ai_process.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


@pytest.mark.skipif(os.name!='nt',reason='Actual Windows job containment')
@pytest.mark.parametrize('close_owner',[False,True])
def test_venv_actual_interpreter_and_descendant_owned_without_model(tmp_path,close_owner):
    python=ROOT/'tmp/selfhost-ai-runtime/bge-142-cpu/.venv/Scripts/python.exe'
    if not python.is_file():pytest.skip('Named approved local runtime unavailable')
    job=m.RequestJob();marker=tmp_path/'ready.json'
    code=("import sys,os,subprocess,time,json;from pathlib import Path;"
          "sys.path.insert(0,sys.argv[1]);from local_ai_process import join_request_job;"
          "join_request_job(sys.argv[2]);"
          "p=subprocess.Popen([sys.executable,'-I','-c','import time;time.sleep(20)']);"
          "Path(sys.argv[3]).write_text(json.dumps({'actual':os.getpid(),'descendant':p.pid}));"
          "time.sleep(20)")
    p=None
    try:
        p=subprocess.Popen([str(python),'-I','-c',code,str(ROOT/'scripts/ops'),job.name,str(marker)],
                           stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,creationflags=subprocess.CREATE_NO_WINDOW)
        end=time.monotonic()+8
        while not marker.exists() and p.poll() is None and time.monotonic()<end:time.sleep(.05)
        assert marker.exists(),p.communicate(timeout=1)[1].decode(errors='replace') if p.poll() is not None else 'No ownership handshake'
        proof=json.loads(marker.read_text());assert proof['actual']!=p.pid  # Actual venv trampoline reproduced.
        assert job.active()>=2
        if close_owner:job.close()
        else:
            job.terminate_and_confirm(seconds=5)
            assert job.active()==0
        p.wait(timeout=5)
    finally:
        if job.handle:
            job.terminate_and_confirm(seconds=5);job.close()
        if p is not None and p.poll() is None:p.wait(timeout=5)


def test_missing_job_refuses_before_any_model_import():
    with pytest.raises(RuntimeError,match='REQUEST_JOB_REQUIRED'):m.join_request_job('')
