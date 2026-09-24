"""024 additive normal API negatives and scheduling; never SQL business writes.

Page flow precedes this suite. API requests are evidence only of API behavior.
All credentials and phone values stay in ignored private context, not evidence.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
import json
from pathlib import Path
import runpy
import secrets
import time

ROOT=Path(__file__).resolve().parents[2]
h=runpy.run_path(str(ROOT/'reports/notification-events-inbox-013/run-runtime.py'))
from auth001_isolated_runtime import build_isolated_settings
from app.core.database import assert_isolated_auth_database
settings=build_isolated_settings();assert_isolated_auth_database(settings,database_url=settings.active_database_url)
private=ROOT/'tmp/maintenance-contact-dispatch-024/context.json'
ctx=json.loads(private.read_text(encoding='utf-8'))
out=Path(__file__).with_name('api-evidence.json')
e=json.loads(out.read_text(encoding='utf-8')) if out.exists() else {'run_id':ctx['run_id'],'mode':'normal API; NOT UI or real-model evidence','steps':{},'requests':[]}
c=h['Client'](ctx['run_id']+'-api')
def save():
    e['requests'].extend(c.requests);c.requests.clear()
    out.write_text(json.dumps(e,ensure_ascii=False,indent=2),encoding='utf-8')
    private.write_text(json.dumps(ctx,ensure_ascii=False,indent=2),encoding='utf-8')
def step(name,fn):
    if name not in e['steps']:
        e['steps'][name]=fn();save();print(json.dumps({'step':name,'status':'verified'}),flush=True)
    return e['steps'][name]
def req(method,path,actor='resident',**kwargs):
    return c.data(method,path,token=ctx['tokens'][actor],label=kwargs.pop('label',path.replace('/','-')),**kwargs)
def detail(oid): return req('GET',f'/work-orders/{oid}')
def wait(oid,status):
    for _ in range(60):
        d=detail(oid)
        if d['work_order']['status']==status:return d
        time.sleep(.5)
    raise RuntimeError(f'order {oid} not {status}; is '+d['work_order']['status'])
def active(d):return next(a for a in d['assignments'] if a['status']=='ACTIVE')
def finish(oid,actor):
    d=detail(oid)
    for status,endpoint in [('ASSIGNED','accept'),('ACCEPTED','start')]:
        if d['work_order']['status']==status:
            req('POST',f'/staff/work-orders/{oid}/{endpoint}',actor,body={'version':d['work_order']['version']});d=detail(oid)
    if d['work_order']['status']=='PROCESSING':
        req('POST',f'/staff/work-orders/{oid}/receipts',actor,body={'version':d['work_order']['version'],'problem_type':'普通维修','method':'合成验收：逐项修复电气和滴水现象，逐项复检正常。','resolved':True})
    d=detail(oid)
    if d['work_order']['status']=='WAIT_CONFIRM':req('POST',f'/work-orders/{oid}/confirm',body={'version':d['work_order']['version']})
    return {'id':oid,'status':wait(oid,'CLOSED')['work_order']['status']}
def contact_checks():
    for label,phone in [('empty',''),('format','not-a-phone'),('unicode','１３８００００００００'),('missing',None)]:
        body={**ctx['createBody']['a']}
        if phone is None:body.pop('contact_phone',None)
        else:body['contact_phone']=phone
        c.call('POST','/work-orders',token=ctx['tokens']['resident'],body=body,headers={'Idempotency-Key':ctx['run_id']+'-invalid-'+label},expected=(422,),label='contact-'+label)
    oid=ctx['orders']['a'];key=ctx['createKeys']['a'];body=ctx['createBody']['a']
    replay=req('POST','/work-orders',body=body,headers={'Idempotency-Key':key},label='same-key-replay')
    assert replay.get('id',replay.get('work_order',{}).get('id'))==oid
    c.call('POST','/work-orders',token=ctx['tokens']['resident'],body={**body,'contact_phone':ctx['phones']['staff2']},headers={'Idempotency-Key':key},expected=(409,),label='different-contact-conflict')
    for actor,order in [('outsider',ctx['orders']['b']),('staff2',ctx['orders']['b']),('staff1',oid)]:
        c.call('GET',f'/work-orders/{order}/contact?reveal=true',token=ctx['tokens'][actor],expected=(403,),label=actor+'-unauthorized-'+str(order))
    for actor in ('resident','staff1','manager'):
        value=req('GET',f"/work-orders/{ctx['orders']['b']}/contact?reveal=true",actor)
        assert value['phone']=='+86'+ctx['phones']['resident'].removeprefix('+86')
    data=detail(oid)
    assert 'contact_phone_e164' not in json.dumps(data) and 'contact_phone' not in json.dumps(data)
    r=c.http.get(f'/work-orders/{oid}/contact',headers={'Authorization':'Bearer '+ctx['tokens']['resident']})
    assert r.headers.get('cache-control')=='no-store' and r.json()['data']['phone'] is None
    return {'required_format_unicode':True,'same_key_same_id':oid,'different_contact_conflict':True,'outsider_unrelated_staff_previous_assignee_denied':True,'three_authorized_actors':True,'common_dto_phone_free':True,'cache_control':'no-store'}
def concurrent_creates():
    def one(label,house):
        client=h['Client'](ctx['run_id']+'-'+label)
        body={'house_id':house,'text':ctx['run_id']+' '+label+' 普通维修：水龙头滴水，无危险迹象。','contact_phone':ctx['phones']['resident']}
        try:
            data=client.data('POST','/work-orders',token=ctx['tokens']['resident'],body=body,headers={'Idempotency-Key':ctx['run_id']+'-'+label},label='concurrent-create')
            return label,data.get('id',data.get('work_order',{}).get('id')),client.requests
        finally:client.close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(one,label,ctx['houses'][house]) for label,house in [('d','a'),('e','b')]]
        for f in futures:
            label,oid,requests=f.result();ctx['orders'][label]=oid;e['requests'].extend(requests);save()
    result={label:wait(ctx['orders'][label],'PENDING_ASSIGNMENT')['work_order']['id'] for label in ('d','e')}
    return {'orders':result,'both_busy_persisted':True,'worker_pool':'solo; concurrent HTTP creation, not parallel Worker transaction stress'}
def release_b():
    result=finish(ctx['orders']['b'],'staff1')
    # Concurrent requests have no caller-label ordering contract. Observe the
    # actual allocated IDs/order, not assume the thread named d arrived first.
    for _ in range(60):
        states={label:detail(ctx['orders'][label]) for label in ('d','e')}
        assigned=[label for label,d in states.items() if d['work_order']['status']=='ASSIGNED']
        if len(assigned)==1:break
        time.sleep(.5)
    assert len(assigned)==1
    first=assigned[0];second=next(label for label in ('d','e') if label!=first)
    ctx['first_released_label']=first;ctx['second_released_label']=second;save()
    d=states[first]
    assert active(d)['employee_id']==ctx['employees']['staff1']
    assert states[second]['work_order']['status']=='PENDING_ASSIGNMENT'
    return {**result,'next_auto':ctx['orders'][first],'single_freed_slot_not_overassigned':True}
def release_c():
    result=finish(ctx['orders']['c'],'staff2')
    oid=ctx['orders'][ctx['second_released_label']]
    d=wait(oid,'ASSIGNED');assert active(d)['employee_id']==ctx['employees']['staff2']
    return {**result,'next_auto':oid}
def reject_and_resume():
    oid=ctx['orders'][ctx['first_released_label']];d=detail(oid)
    if d['work_order']['status']=='ASSIGNED':
        req('POST',f'/staff/work-orders/{oid}/reject','staff1',body={'version':d['work_order']['version'],'reason':'合成验证：本次无法继续接单，保持系统重派与历史。'})
    wait(oid,'PENDING_ASSIGNMENT')
    finish(ctx['orders'][ctx['second_released_label']],'staff2')
    d=wait(oid,'ASSIGNED');assert active(d)['employee_id']==ctx['employees']['staff2']
    return {'order':oid,'rejected_employee':ctx['employees']['staff1'],'next_auto_employee':ctx['employees']['staff2'],'history_retained':len(d['assignments'])>=2}
def end_assignment():
    emp=ctx['employees']['staff2']
    d=req('GET',f'/admin/employees/{emp}','manager')
    appointments=[a for a in d['assignments'] if a['property_id']==ctx['property_id']]
    appointment=max(appointments,key=lambda a:a['id'])
    if appointment['status']=='ACTIVE':
        req('POST',f"/admin/employees/{emp}/property-assignments/{appointment['id']}/end",'manager',body={})
    else:assert appointment['status']=='ENDED'
    oid=ctx['orders'][ctx['first_released_label']];wait(oid,'PENDING_ASSIGNMENT')
    c.call('GET',f'/work-orders/{oid}/contact?reveal=true',token=ctx['tokens']['staff2'],expected=(403,),label='ended-staff-contact-denied')
    status,payload=c.call('POST','/staff/attendance/checkin',token=ctx['tokens']['staff2'],body={'property_id':ctx['property_id']},expected=(409,),label='ended-checkin-denied')
    assert h['_error_code'](payload)=='EMP.CHECKIN_DENIED'
    return {'employee':emp,'appointment':appointment['id'],'order':oid,'waiting':True,'contact_and_checkin_denied':True}
def reappoint():
    emp=ctx['employees']['staff2']
    req('POST',f'/admin/employees/{emp}/property-assignments','manager',body={'property_id':ctx['property_id']})
    oid=ctx['orders'][ctx['first_released_label']]
    d=wait(oid,'ASSIGNED')
    assert active(d)['employee_id']==emp
    return {'order':oid,'new_auto_assignment':active(d)['id'],'employee':emp}
def phone_change():
    old=ctx['phones']['resident'];new=ctx.setdefault('new_resident_phone','199'+str(secrets.randbelow(100_000_000)).zfill(8));save()
    def otp(phone,label):
        ch=c.data('POST','/auth/challenge',body={'phone':phone,'purpose':'login'},label=label+'-challenge')
        c.data('POST','/auth/sms/send',body={'phone':phone,'purpose':'change_phone','challenge_id':ch['challenge_id'],'challenge_answer':h['_challenge_answer'](ch['prompt'])},label=label+'-send')
        return c.data('POST','/auth/sms/test-inbox',body={'phone':phone,'purpose':'change_phone'},label=label+'-inbox')['code']
    result=req('POST','/auth/phone/change',body={'old_phone':old,'old_code':otp(old,'old'),'new_phone':new,'new_code':otp(new,'new')})
    ctx['tokens']['resident']=result['access_token'];ctx['original_resident_phone']=old;ctx['phones']['resident']=new;save()
    me=req('GET','/me');assert me['user_id']==ctx['identities']['resident']
    current=req('GET','/me/contact-phone')
    snapshot=req('GET',f"/work-orders/{ctx['orders']['a']}/contact?reveal=true")
    assert snapshot['phone']=='+86'+old.removeprefix('+86')
    assert new.removeprefix('+86') in json.dumps(current)
    return {'same_user_id':me['user_id'],'current_phone_changed':True,'historical_order_contact_unchanged':True,'values':'redacted'}
def fair_rotation():
    first=ctx['orders'][ctx['first_released_label']]
    finish(first,'staff2')
    results=[]
    for label,actor,house in [('f','staff1','a'),('g','staff2','c')]:
        if label not in ctx['orders']:
            body={'house_id':ctx['houses'][house],'text':ctx['run_id']+' '+label+' 普通维修插座不供电，无烟火。',
                  'contact_phone':ctx['phones']['staff1']}
            created=req('POST','/work-orders',body=body,headers={'Idempotency-Key':ctx['run_id']+'-rotation-'+label})
            ctx['orders'][label]=created.get('id',created.get('work_order',{}).get('id'));save()
        oid=ctx['orders'][label];d=detail(oid)
        if d['work_order']['status']=='PENDING_ANALYSIS':d=wait(oid,'ASSIGNED')
        assignee=next(a for a in d['assignments'] if a['employee_id']==ctx['employees'][actor])
        assert assignee['assigned_by_user_id'] is None
        phone=req('GET',f'/work-orders/{oid}/contact?reveal=true')['phone']
        assert phone=='+86'+ctx['phones']['staff1'].removeprefix('+86')
        assert new_account_phone()==ctx['phones']['resident'].removeprefix('+86')
        results.append({'order':oid,'auto_employee':assignee['employee_id'],'alternate_contact_did_not_change_identity':True})
        finish(oid,actor)
    return {'both_idle_least_recent_rotation':results}
def new_account_phone():
    value=req('GET','/me/contact-phone')
    return value['phone'].removeprefix('+86')
try:
    assert {'a','b','c'} <= ctx['orders'].keys(), 'Complete page cases first'
    for actor in ('resident','staff1','staff2','manager','outsider'):
        status,_=c.call('GET','/me',token=ctx['tokens'][actor],label=actor+'-reuse-session',expected=(200,401))
        if status==200:continue
        remaining=65-(time.time()-ctx.get('last_sms_sent_at',{}).get(actor,0)/1000)
        if remaining>0:time.sleep(remaining)
        ctx['tokens'][actor]=c.sms_login(ctx['phones'][actor],actor+'-normal-auth')['access_token']
        ctx['last_sms_sent_at'][actor]=int(time.time()*1000);save()
    step('contact-privacy-idempotency',contact_checks)
    step('concurrent-create-busy',concurrent_creates)
    step('release-b-auto-d',release_b)
    step('release-c-auto-e',release_c)
    step('reject-history-release-resume',reject_and_resume)
    step('end-appointment-auto-release',end_assignment)
    step('reappoint-auto-resume',reappoint)
    step('both-idle-fair-rotation',fair_rotation)
    step('phone-rebind-independent-snapshot',phone_change)
    e['status']='API_CHECKS_COMPLETED';save()
except Exception as exc:
    e.setdefault('attempt_errors',[]).append({'type':type(exc).__name__,'message':str(exc)})
    save();raise
finally:save();c.close()
