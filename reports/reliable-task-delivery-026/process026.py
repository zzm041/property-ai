"""Precise owned test-process launch/termination and durable counters."""
import subprocess
import time
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parent))
from common026 import *
def identity(pid):
    command=f"Get-CimInstance Win32_Process -Filter 'ProcessId={int(pid)}' | Select-Object ProcessId,ExecutablePath,CommandLine,@{{N='created';E={{$_.CreationDate.ToString('o')}}}} | ConvertTo-Json -Compress"
    raw=subprocess.check_output(['powershell','-NoProfile','-Command',command],text=True).strip()
    return json.loads(raw) if raw else None
def start(component,label):
    ctx=context();assert label not in ctx['processes'],'Never start a recorded process twice'
    args=[sys.executable,'-I','-X','utf8',str(OUT/'launch-026.py'),component,'--run-id',ctx['run_id'],'--label',label]
    stdout=PRIVATE/(label+'.stdout.log');stderr=PRIVATE/(label+'.stderr.log')
    with stdout.open('xb') as a,stderr.open('xb') as b:
        p=subprocess.Popen(args,cwd=ROOT,stdout=a,stderr=b,creationflags=subprocess.CREATE_NO_WINDOW)
    time.sleep(.2);record=identity(p.pid);assert record is not None
    ctx['processes'][label]={'component':component,'identity':record};save_context(ctx)
    return p.pid
def kill(label):
    ctx=context();record=ctx['processes'][label];actual=identity(record['identity']['ProcessId'])
    assert actual==record['identity'] and ctx['run_id'] in actual['CommandLine']
    assert actual['ExecutablePath'].lower()==sys.executable.lower()
    key='worker' if record['component']=='worker' else 'api_publisher'
    assert ctx['kills'][key]<(2 if key=='worker' else 3)
    assert (datetime.now(UTC)-datetime.fromisoformat(ctx['drill_started'])).total_seconds()<900
    ctx['kills'][key]+=1;record['termination_reserved_utc']=stamp();save_context(ctx)
    # Exact verified process only. Reservation happens first, never reset on unknown outcome.
    subprocess.run(['powershell','-NoProfile','-Command',f"Stop-Process -Id {actual['ProcessId']} -ErrorAction Stop"],check=True)
    end=time.monotonic()+5
    while identity(actual['ProcessId']) is not None and time.monotonic()<end:time.sleep(.2)
    assert identity(actual['ProcessId']) is None
    ctx=context();ctx['processes'][label]['terminated_utc']=stamp();save_context(ctx)
    return {'identity':actual,'method':'real Windows process termination','terminated':True,'counters':ctx['kills']}
if __name__=='__main__':
    print(json.dumps({'pid':start(sys.argv[1],sys.argv[2])}))
