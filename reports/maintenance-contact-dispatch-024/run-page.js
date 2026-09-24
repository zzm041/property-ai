"use strict";
// Actual controls and origin-forwarded requests. No page.setData or fake API data.
const fs=require('fs'), path=require('path'), http=require('http'), assert=require('assert/strict');
const ROOT='D:/property-ai', PRIVATE=path.join(ROOT,'tmp/maintenance-contact-dispatch-024/context.json');
const ctx=JSON.parse(fs.readFileSync(PRIVATE,'utf8')), OUT=path.join(__dirname,'page-evidence.json');
const e=fs.existsSync(OUT)?JSON.parse(fs.readFileSync(OUT,'utf8')):{task_id:'MAINTENANCE-CONTACT-DISPATCH-024',run_id:ctx.run_id,steps:{},requests:[],screenshots:[]};
const sleep=ms=>new Promise(r=>setTimeout(r,ms)); let mp,observing=false;
function scrub(value,key=''){if(/phone|token|authorization|code|credential/i.test(key)&&!['status_code','event_code','error_code'].includes(key))return '[redacted]';if(Array.isArray(value))return value.map(v=>scrub(v));if(value&&typeof value==='object')return Object.fromEntries(Object.entries(value).map(([k,v])=>[k,scrub(v,k)]));if(typeof value==='string'){for(const p of Object.values(ctx.phones))value=value.split(p).join('[redacted-phone]');return value.replace(/eyJ[\w-]+\.[\w-]+\.[\w-]+/g,'[redacted-token]');}return value;}
function save(){fs.writeFileSync(OUT,JSON.stringify(scrub(e),null,2));fs.writeFileSync(PRIVATE,JSON.stringify(ctx,null,2));}
async function current(fragment){for(let i=0;i<120;i++){const p=await mp.currentPage();if(p&&p.path.includes(fragment))return p;await sleep(250);}throw Error('Page timeout '+fragment);}
async function ready(p,test,label){for(let i=0;i<120;i++){const d=await p.data();if(!d.loading&&!d.houseLoading&&test(d))return d;await sleep(250);}throw Error('State timeout '+label+' '+JSON.stringify(scrub(await p.data())));}
async function button(p,label){for(const b of await p.$$('button'))if((await b.text()).trim()===label)return b;throw Error('No button '+label);}
async function tap(b){try{await b.tap();}catch(err){if(!String(err.message).includes('timeout waiting for automator response'))throw err;}}
async function fill(p,placeholder,value,kind='input'){for(const b of await p.$$(kind))if(await b.attribute('placeholder')===placeholder){await b.input(String(value));return;}throw Error('No field '+placeholder);}
async function navigate(url,fragment){await mp.callWxMethod('navigateTo',{url});return current(fragment||url.replace(/^\//,'').split('?')[0]);}
async function home(){await mp.callWxMethod('reLaunch',{url:'/pages/home/home'});const p=await current('pages/home/home');await ready(p,d=>d.me&&d.me.user_id,'home');return p;}
function inbox(actor){return new Promise((resolve,reject)=>{const body=JSON.stringify({phone:ctx.phones[actor],purpose:'login'});const r=http.request({hostname:'127.0.0.1',port:8083,path:'/api/v1/auth/sms/test-inbox',method:'POST',headers:{'Content-Type':'application/json','Content-Length':Buffer.byteLength(body)}},res=>{let raw='';res.on('data',x=>raw+=x);res.on('end',()=>res.statusCode===200?resolve(JSON.parse(raw).data.code):reject(Error('inbox denied')));});r.on('error',reject);r.end(body);});}
async function login(actor){
  let p=await mp.currentPage();if(p&&p.path!=='pages/login/login'){p=await home();await tap(await button(p,'退出登录'));await current('pages/login/login');}
  while(Date.now()-(ctx.last_sms_sent_at[actor]||0)<65000)await sleep(1000);
  await mp.callWxMethod('reLaunch',{url:'/pages/login/login'});p=await current('pages/login/login');
  for(const x of await p.$$('.link-text'))if((await x.text()).includes('验证码登录')){await tap(x);break;}
  await ready(p,d=>d.mode==='sms','sms-mode');await fill(p,'手机号',ctx.phones[actor]);await tap(await button(p,'获取验证码'));
  let d=await ready(p,d=>d.challengePrompt,'challenge');let m=d.challengePrompt.match(/(\d+)\s*\+\s*(\d+)\s*=\s*\?/);assert(m);
  await fill(p,'挑战答案',Number(m[1])+Number(m[2]));await tap(await button(p,'获取验证码'));await ready(p,d=>!d.challengePrompt&&d.cooldown>0,'sms-sent');ctx.last_sms_sent_at[actor]=Date.now();save();
  await fill(p,'短信验证码',await inbox(actor));await tap(await button(p,'登录'));p=await current('pages/home/home');d=await ready(p,d=>d.me&&d.me.user_id,'logged-in');assert.equal(d.me.user_id,ctx.identities[actor]);assert(!d.me.roles.includes('SYSTEM_ADMIN'));
  ctx.tokens[actor]=await mp.callWxMethod('getStorageSync','access_token');e.steps['auth-'+actor]={user_id:d.me.user_id,roles:d.me.roles,source:'normal SMS page; Development OTP inbox'};save();return p;
}
async function snap(p,name){await mp.callWxMethod('pageScrollTo',{scrollTop:0,duration:0});await sleep(350);const d=await p.data();e.steps[name]={path:p.path,workOrder:d.workOrder,statusLabel:d.statusLabel,error:d.error,snapshot:d.snapshot};save();await mp.screenshot({path:path.join(__dirname,name+'.png')});if(!e.screenshots.includes(name+'.png'))e.screenshots.push(name+'.png');save();}
async function stage(name,fn){if(e.steps[name]&&e.steps[name].complete)return;e.phase=name;save();await fn();e.steps[name]={...(e.steps[name]||{}),complete:true};save();console.log(JSON.stringify({stage:name,status:'completed'}));}
async function orderDetail(id,status){let p=await navigate('/pages/work-orders/detail?id='+id);for(let i=0;i<30;i++){const d=await ready(p,d=>d.workOrder&&d.workOrder.id===id,'order-loaded');if(!status||d.workOrder.status===status)return p;await sleep(400);await tap(await button(p,'刷新'));}throw Error('order state did not become '+status);}
async function create(label,negative=false){
 if(ctx.orders&&ctx.orders[label])return orderDetail(ctx.orders[label]);
 let pHome=await home();await tap(await button(pHome,'报修'));let p=await current('pages/work-orders/create');let d=await ready(p,d=>d.houses&&d.houses.some(h=>h.house_id===ctx.houses[label])&&d.contactPhone,'create defaults');
 assert.equal(d.contactPhone.replace(/^\+86/,''),ctx.phones.resident.replace(/^\+86/,''));assert.equal(d.contactConfirmed,false);
 for(const b of await p.$$('.house-choice'))if(Number(await b.attribute('data-id'))===ctx.houses[label]){await tap(b);break;}
 const report=ctx.run_id+'-'+label+'：插座不通电；水龙头滴水。没有烟、火花或焦味，请同一维修负责人统筹两个问题。';
 await fill(p,'报修说明',report,'textarea');
 const placeholder='手机号，或 +国家区号 的固定/移动电话';
 if(negative&&!e.steps.contact_negative){const count=e.requests.filter(r=>r.method==='POST'&&r.path==='/work-orders').length;
  await fill(p,placeholder,'');await tap(await button(p,'提交报修'));await ready(p,d=>d.error.includes('合法联系电话'),'empty denied');await snap(p,'01-phone-required');
  await fill(p,placeholder,'invalid');await tap(await button(p,'提交报修'));await ready(p,d=>d.error.includes('合法联系电话'),'invalid denied');
  await fill(p,placeholder,ctx.phones.resident);await tap(await button(p,'提交报修'));await ready(p,d=>d.error.includes('确认'),'confirmation required');
  assert.equal(e.requests.filter(r=>r.method==='POST'&&r.path==='/work-orders').length,count);
  e.steps.contact_negative={empty:true,invalid:true,unconfirmed:true,no_create_request:true};save();
 }
 await fill(p,placeholder,ctx.phones.resident);await (await p.$('checkbox-group')).trigger('change',{value:['confirmed']});await ready(p,d=>d.contactConfirmed,'confirmed');
 ctx.pendingLabel=label;ctx.pendingReport=report;save();await tap(await button(p,'提交报修'));p=await current('pages/work-orders/detail');d=await ready(p,d=>d.workOrder&&d.workOrder.id,'created');
 assert.equal(ctx.orders[label],d.workOrder.id);delete ctx.pendingLabel;delete ctx.pendingReport;save();return p;
}
async function contact(p,name){
 const component=await p.$('#order-contact');assert(component);
 for(let i=0;i<30;i++){if((await component.data()).visible)break;await sleep(100);}
 let data=await component.data();assert(data.collected&&data.masked&&!data.phone);
 await tap(await button(component,'查看联系电话'));for(let i=0;i<40;i++){data=await component.data();if(data.phone)break;await sleep(100);}
 assert.equal(data.phone.replace(/^\+86/,''),ctx.phones.resident.replace(/^\+86/,''));
 e.steps[name]={authorized_reveal:true,source:'actual contact API; number deliberately not recorded'};save();
 // Real navigation clears plaintext before any screenshot.
 const id=(await p.data()).workOrder.id;await home();p=await orderDetail(id);await sleep(250);await snap(p,name+'-masked');return p;
}
async function main(){
 if(e.error){e.attempt_errors=e.attempt_errors||[];e.attempt_errors.push({phase:e.phase,error:e.error});delete e.error;save();}
 mp=await require(process.env.MP_AUTOMATOR_MODULE).connect({wsEndpoint:'ws://127.0.0.1:9440'});
 await mp.exposeFunction('recordMaintenance024',r=>{if(!/^\/(work-orders|me\/contact|staff\/|supervisor\/|admin\/employees)/.test(r.path))return;e.requests.push({...scrub(r),source:'actual origin-forwarded wx.request',received_at_utc:new Date().toISOString()});
  if(r.path==='/work-orders'&&r.method==='POST'&&r.status===200){assert(ctx.pendingLabel);ctx.orders=ctx.orders||{};ctx.orders[ctx.pendingLabel]=r.data.id||r.data.work_order.id;ctx.createBody=ctx.createBody||{};ctx.createBody[ctx.pendingLabel]=r.input;ctx.createKeys=ctx.createKeys||{};ctx.createKeys[ctx.pendingLabel]=r.key;}
  save();});
 await mp.mockWxMethod('request',function(o){const self=this;if(!o.url.startsWith('http://127.0.0.1:8083/api/v1/'))throw Error('Only isolated8083 allowed');if(/\/work-orders\/12(?:\/|\?|$)/.test(o.url)&&(o.method||'GET')!=='GET')throw Error('Protected historical order');
 return new Promise((resolve,reject)=>self.origin({...o,success(r){recordMaintenance024({path:o.url.slice('http://127.0.0.1:8083/api/v1'.length),method:o.method||'GET',input:o.data,key:o.header&&o.header['Idempotency-Key'],status:r.statusCode,request_id:r.data.request_id,data:r.data.data});if(o.success)o.success(r);resolve(r);},fail(err){if(o.fail)o.fail(err);reject(err);}}));});observing=true;
 if(process.argv.includes('--supplement-only')){
  await login('resident');let p=await orderDetail(ctx.orders.a,'CLOSED');const comp=await p.$('#order-contact');await ready(comp,d=>d.collected,'contact restored');
  await tap(await button(comp,'查看联系电话'));await ready(comp,d=>d.phone,'historic snapshot');assert.equal((await comp.data()).phone.replace(/^\+86/,''),ctx.original_resident_phone.replace(/^\+86/,''));
  await home();p=await orderDetail(ctx.orders.a,'CLOSED');await snap(p,'14-relogin-historical-snapshot-masked');
  p=await navigate('/pages/work-orders/create');await ready(p,d=>d.contactPhone&&d.houses.length,'new default');assert.equal((await p.data()).contactPhone.replace(/^\+86/,''),ctx.phones.resident.replace(/^\+86/,''));
  for(const b of await p.$$('.house-choice'))if(Number(await b.attribute('data-id'))===ctx.houses.a){await tap(b);break;}
  await fill(p,'报修说明','仅展示必填校验，不提交工单','textarea');await fill(p,'手机号，或 +国家区号 的固定/移动电话','');await tap(await button(p,'提交报修'));await ready(p,d=>d.error.includes('合法联系电话'),'required');
  await mp.callWxMethod('pageScrollTo',{scrollTop:100000,duration:0});await sleep(350);await mp.screenshot({path:path.join(__dirname,'15-contact-form-required-visible.png')});e.screenshots.push('15-contact-form-required-visible.png');
  e.steps['supplement-relogin-contact']={same_closed_order:ctx.orders.a,old_snapshot_unchanged:true,new_form_uses_current_phone:true,create_request_count:0,phone_values_not_saved:true};save();await home();return;
 }
 const businessDate=new Date().toLocaleDateString('sv-SE',{timeZone:'Asia/Shanghai'});
 await stage('staff1-checkin-property-'+ctx.property_id+'-'+businessDate,async()=>{const pHome=await login('staff1');await tap(await button(pHome,'今日考勤'));const p=await current('pages/attendance/attendance');await fill(p,'小区 ID',ctx.property_id);await tap(await button(p,'签到'));await ready(p,d=>d.snapshot&&d.snapshot.daily_status&&d.snapshot.daily_status.property_id===ctx.property_id,'attendance');await snap(p,'02-current-duty-property-'+ctx.property_id+'-'+businessDate);});
 await stage('create-a',async()=>{await login('resident');let p=await create('a',true);p=await orderDetail(ctx.orders.a,'ASSIGNED');await snap(p,'03-single-member-auto');await contact(p,'04-resident-contact');});
 await stage('create-b-waiting',async()=>{let p=await create('b');p=await orderDetail(ctx.orders.b,'PENDING_ASSIGNMENT');let d=await p.data();assert(d.logs.some(x=>x.event_code==='AUTO_DISPATCH_WAIT'));await snap(p,'05-all-busy-persisted');});
 await stage('staff1-process-a',async()=>{await login('staff1');let p=await navigate('/pages/staff-orders/detail?id='+ctx.orders.a);await ready(p,d=>d.canAccept,'assigned task');let d=await p.data();assert(d.item.text.includes('水龙头')&&d.item.text.includes('插座'));await snap(p,'06-all-issues-staff');
  await tap(await button(p,'接单'));await ready(p,d=>d.canStart,'accepted');await tap(await button(p,'开始处理'));await ready(p,d=>d.canFinish,'processing');
  await fill(p,'例如：电气维修','普通维修');await fill(p,'逐项说明全部报修问题的处理结果，不遗漏次要事项','合成验证：已修复插座接触问题并恢复供电；已修复水龙头密封并验证不再滴水。','textarea');
  await tap(await button(p,'提交完整回执并完成'));await ready(p,d=>d.workOrder&&d.workOrder.status==='WAIT_CONFIRM','receipt');await snap(p,'07-full-receipt');});
 await stage('resident-confirm-a-auto-b',async()=>{await login('resident');let p=await orderDetail(ctx.orders.a);if((await p.data()).workOrder.status!=='CLOSED'){assert.equal((await p.data()).workOrder.status,'WAIT_CONFIRM');await tap(await button(p,'确认解决'));await ready(p,d=>d.workOrder&&d.workOrder.status==='CLOSED','closed');}await snap(p,'08-closed-a');p=await orderDetail(ctx.orders.b,'ASSIGNED');await snap(p,'09-release-auto-b');});
 await stage('staff-current-contact',async()=>{await login('staff1');const p=await navigate('/pages/staff-orders/detail?id='+ctx.orders.b);await ready(p,d=>d.workOrder&&d.workOrder.id===ctx.orders.b,'staff task');const comp=await p.$('#order-contact');await ready(comp,d=>d.visible&&d.masked,'staff masked');await snap(p,'10-staff-contact-masked');await tap(await button(comp,'查看联系电话'));await ready(comp,d=>d.phone,'staff revealed');assert.equal((await comp.data()).phone.replace(/^\+86/,''),ctx.phones.resident.replace(/^\+86/,''));e.steps.staff_contact={authorized_reveal:true};await home();});
 await stage('manager-current-contact',async()=>{await login('manager');const p=await navigate('/pages/supervisor/queue?tenantId='+ctx.tenant_id+'&propertyId='+ctx.property_id);await ready(p,d=>d.tenantId,'manager scope');await tap(await button(p,'未开工工单'));await ready(p,d=>d.items.some(x=>x.id===ctx.orders.b),'manager queue');for(const item of await p.$$('.card'))if(Number(await item.attribute('data-id'))===ctx.orders.b){await tap(item);break;}await ready(p,d=>d.selectedId===ctx.orders.b,'manager selected');const comp=await p.$('#order-contact');await ready(comp,d=>d.visible&&d.masked,'manager masked');await snap(p,'11-manager-contact-masked');await tap(await button(comp,'查看联系电话'));await ready(comp,d=>d.phone,'manager revealed');assert.equal((await comp.data()).phone.replace(/^\+86/,''),ctx.phones.resident.replace(/^\+86/,''));e.steps.manager_contact={authorized_reveal:true};await home();});
 await stage('staff2-checkin',async()=>{const ph=await login('staff2');await tap(await button(ph,'今日考勤'));const p=await current('pages/attendance/attendance');await fill(p,'小区 ID',ctx.property_id);await tap(await button(p,'签到'));await ready(p,d=>d.snapshot&&d.snapshot.daily_status.property_id===ctx.property_id,'staff2 attendance');await snap(p,'12-staff2-current-duty');});
 await stage('create-c-idle-priority',async()=>{await login('resident');let p=await create('c');p=await orderDetail(ctx.orders.c,'ASSIGNED');await snap(p,'13-idle-staff2-auto');});
 e.status='PRIMARY_PAGE_FLOW_COMPLETED_PENDING_API_DB_AND_REVIEW';save();console.log(JSON.stringify({status:e.status,orders:ctx.orders,screenshots:e.screenshots.length}));
}
main().catch(err=>{e.error=scrub(String(err.stack||err));save();console.error(e.error);process.exitCode=1;}).finally(async()=>{if(mp){if(observing)await mp.restoreWxMethod('request').catch(()=>{});await mp.disconnect();}});
