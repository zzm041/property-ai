"""Exercise corrected appointment API, preserving the old rows as ENDED history."""
import json
from pathlib import Path
import runpy

ROOT = Path(__file__).resolve().parents[2]
h = runpy.run_path(str(ROOT/'reports/notification-events-inbox-013/run-runtime.py'))
ctx = json.loads((ROOT/'tmp/maintenance-contact-dispatch-024/context.json').read_text(encoding='utf-8'))
out = Path(__file__).with_name('appointment-repair.json')
e = json.loads(out.read_text(encoding='utf-8')) if out.exists() else {'run_id':ctx['run_id'],'steps':{},'requests':[]}
c = h['Client'](ctx['run_id']+'-appointment-fix')
def save():
    e['requests'].extend(c.requests); c.requests.clear()
    out.write_text(json.dumps(e,ensure_ascii=False,indent=2),encoding='utf-8')
try:
    ctx['tokens']['manager']=c.sms_login(ctx['phones']['manager'],'manager-auth')['access_token']
    (ROOT/'tmp/maintenance-contact-dispatch-024/context.json').write_text(json.dumps(ctx,ensure_ascii=False,indent=2),encoding='utf-8')
    for actor in ('staff1','staff2'):
        if actor in e['steps']: continue
        token=ctx['tokens']['manager']; employee=ctx['employees'][actor]
        detail=c.data('GET',f'/admin/employees/{employee}',token=token,label=actor+'-before')
        if actor+'-ended' not in e['steps']:
            active=[a for a in detail['assignments'] if a['property_id']==ctx['property_id'] and a['status']=='ACTIVE']
            assert len(active)==1
            result=c.data('POST',f"/admin/employees/{employee}/property-assignments/{active[0]['id']}/end",token=token,body={},label=actor+'-end')
            e['steps'][actor+'-ended']=result;save()
        e['steps'][actor]=c.data('POST',f'/admin/employees/{employee}/property-assignments',token=token,body={'property_id':ctx['property_id']},label=actor+'-reappoint');save()
    print(json.dumps({'run_id':ctx['run_id'],'status':'normal appointment API correction completed','employees':ctx['employees']}))
finally: save(); c.close()
