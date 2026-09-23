"use strict";
// Actual controls and origin-forwarded wx.request; never invent page/server data.
const fs=require('fs'),path=require('path'),http=require('http'),assert=require('assert/strict'),crypto=require('crypto');
const ROOT='D:/property-ai',PRIVATE=path.join(ROOT,'tmp/selfhost-ai-local/business-context.json');
const ctx=JSON.parse(fs.readFileSync(PRIVATE,'utf8')),OUT=path.join(__dirname,'business-page.json');
const resume=process.argv.includes('--resume-before-create');
assert(resume?fs.existsSync(OUT):!fs.existsSync(OUT),'Checkpoint state mismatch');
const e=resume?JSON.parse(fs.readFileSync(OUT,'utf8')):{task_id:'SELFHOST-AI-LOCAL-001',related_slice:'RESIDENT-HOUSE-SELECT-023',run_id:ctx.run_id,code_sha:ctx.code_sha,model:'real local qwen3.5:2b',steps:{},requests:[],screenshots:[]};
if(resume){assert(!ctx.work_order_id&&!e.requests.some(r=>r.method==='POST'));assert(e.steps['02-selected-report-address']);e.initial_capture_error=e.last_error;e.resume_note='Resumed existing selected-address page after screenshot acknowledgement timeout; no prior create request, no repeated login or house selection.';}
let mp,logging=false;const sleep=ms=>new Promise(r=>setTimeout(r,ms));
function save(){fs.writeFileSync(OUT,JSON.stringify(e,null,2));fs.writeFileSync(PRIVATE,JSON.stringify(ctx,null,2));}
async function current(fragment){for(let i=0;i<100;i++){const p=await mp.currentPage();if(p&&p.path.includes(fragment))return p;await sleep(200);}throw Error('Page timeout '+fragment);}
async function ready(p,test,label){for(let i=0;i<180;i++){const d=await p.data();if(!d.loading&&!d.houseLoading&&test(d))return d;if(i>4&&!d.loading&&(d.error||d.houseError))throw Error(label+': '+(d.error||d.houseError));await sleep(200);}throw Error('State timeout '+label);}
async function button(p,label){for(const x of await p.$$('button'))if((await x.text()).trim()===label)return x;throw Error('Missing button '+label);}
async function fill(p,selector,placeholder,value){for(const x of await p.$$(selector))if(await x.attribute('placeholder')===placeholder){await x.input(String(value));await sleep(150);return;}throw Error('Missing input '+placeholder);}
async function tap(x){try{await x.tap();}catch(err){if(!String(err.message).includes('timeout waiting for automator response'))throw err;await sleep(700);}}
function inbox(){return new Promise((resolve,reject)=>{const body=JSON.stringify({phone:ctx.phones.resident,purpose:'login'}),r=http.request({hostname:'127.0.0.1',port:8083,path:'/api/v1/auth/sms/test-inbox',method:'POST',headers:{'Content-Type':'application/json','Content-Length':Buffer.byteLength(body)}},res=>{let raw='';res.on('data',c=>raw+=c);res.on('end',()=>{if(res.statusCode!==200)return reject(Error('SMS inbox unavailable'));resolve(JSON.parse(raw).data.code);});});r.on('error',reject);r.end(body);});}
async function login(){
 let remain=65000-(Date.now()-(ctx.last_sms_sent_at.resident||0));while(remain>0){console.log(JSON.stringify({stage:'normal-SMS-cooldown',seconds:Math.ceil(remain/1000)}));await sleep(Math.min(remain,20000));remain=65000-(Date.now()-(ctx.last_sms_sent_at.resident||0));}
 await mp.callWxMethod('clearStorageSync');await mp.callWxMethod('reLaunch',{url:'/pages/login/login'});let p=await current('pages/login/login');
 for(const x of await p.$$('.link-text'))if((await x.text()).includes('验证码登录')){await tap(x);break;}
 await ready(p,d=>d.mode==='sms','SMS mode');await fill(p,'input','手机号',ctx.phones.resident);await tap(await button(p,'获取验证码'));
 let d=await ready(p,s=>s.challengePrompt,'challenge'),m=d.challengePrompt.match(/(\d+)\s*\+\s*(\d+)\s*=\s*\?/);assert(m);
 await fill(p,'input','挑战答案',Number(m[1])+Number(m[2]));await tap(await button(p,'获取验证码'));await ready(p,s=>!s.challengePrompt&&s.cooldown>0,'SMS sent');ctx.last_sms_sent_at.resident=Date.now();save();
 await fill(p,'input','短信验证码',await inbox());await tap(await button(p,'登录'));p=await current('pages/home/home');d=await ready(p,s=>s.me&&s.me.user_id,'home');assert.equal(d.me.user_id,ctx.identities.resident);assert(!d.me.roles.includes('SYSTEM_ADMIN'));
 ctx.resident_token=await mp.callWxMethod('getStorageSync','access_token');e.principal={user_id:d.me.user_id,roles:d.me.roles,scopes:d.me.scopes};save();return p;
}
async function snap(p,name){await mp.callWxMethod('pageScrollTo',{scrollTop:0,duration:0});await sleep(700);const d=await p.data();e.steps[name]={path:p.path,houses:d.houses,houseId:d.houseId,selectedHouseLabel:d.selectedHouseLabel,workOrder:d.workOrder,statusLabel:d.statusLabel,assignments:d.assignments,logs:d.logs,error:d.error,buttons:await Promise.all((await p.$$('button')).map(x=>x.text()))};save();const file=name+'.png';await mp.screenshot({path:path.join(__dirname,file)});e.screenshots.push(file);save();}
async function main(){
 assert.equal(ctx.code_sha,'e65b4d7fb0e9541695fdcb9a1f1b216d97dbf6c2');
 mp=await require(process.env.MP_AUTOMATOR_MODULE).connect({wsEndpoint:'ws://127.0.0.1:9440'});
 await mp.exposeFunction('recordLocalQwen',r=>{assert(r.url.startsWith('http://127.0.0.1:8083/api/v1/'));const endpoint=r.url.slice('http://127.0.0.1:8083/api/v1'.length);if(!/^\/(work-orders|me\/houses)/.test(endpoint))return;const method=r.method||'GET';assert(method==='GET'||(method==='POST'&&endpoint==='/work-orders'),'No analysis/assignment/lifecycle shortcut allowed');
  if(method==='POST'&&r.status>=200&&r.status<300){const id=r.data.id||(r.data.work_order&&r.data.work_order.id);assert(id);assert(!ctx.work_order_id||ctx.work_order_id===id,'No duplicate report');ctx.work_order_id=id;ctx.create_request_id=r.id;}
  e.requests.push({path:endpoint,method,status:r.status,request_id:r.id,result:r.data,received_at_utc:new Date().toISOString(),source:'real origin-forwarded wx.request'});save();});
 await mp.mockWxMethod('request',function(o){const self=this;return new Promise((resolve,reject)=>self.origin({...o,success(r){recordLocalQwen({url:o.url,method:o.method,status:r.statusCode,id:r.data.request_id,data:r.data.data});if(o.success)o.success(r);resolve(r);},fail(err){if(o.fail)o.fail(err);reject(err);}}));});logging=true;
 let home,p,d;
 if(resume){p=await current('pages/work-orders/create');d=await ready(p,s=>Number(s.houseId)===ctx.house_id&&s.houses.some(h=>h.house_id===ctx.house_id),'same selected address');await snap(p,'02-selected-report-address-resume');}
 else{home=await login();await tap(await button(home,'报修'));p=await current('pages/work-orders/create');d=await ready(p,s=>s.houses&&s.houses.length>0,'authorized full addresses');assert.equal(d.houseId,'');await snap(p,'01-authorized-address-choice');
 let choice;for(const x of await p.$$('.house-choice'))if(Number(await x.attribute('data-id'))===ctx.house_id){choice=x;break;}assert(choice);await tap(choice);d=await ready(p,s=>Number(s.houseId)===ctx.house_id,'select actual house');assert(d.selectedHouseLabel.includes(' · '));await snap(p,'02-selected-report-address');}
 ctx.report_text=ctx.run_id+' 合成资料：厨房插座没有电，其他房间照明正常，没有冒烟，请维修。';save();await fill(p,'textarea','报修说明',ctx.report_text);assert(!ctx.work_order_id);await tap(await button(p,'提交报修'));
 p=await current('pages/work-orders/detail');d=await ready(p,s=>s.workOrder&&s.workOrder.id===ctx.work_order_id,'same new report');
 for(let i=0;i<25&&d.workOrder.status!=='ASSIGNED';i++){await sleep(2000);await tap(await button(p,'刷新'));d=await ready(p,s=>s.workOrder&&s.workOrder.id===ctx.work_order_id,'automatic analysis');}
 assert.equal(d.workOrder.status,'ASSIGNED');assert(d.workOrder.current_assignment_id);await snap(p,'03-real-qwen-auto-assigned');
 await tap(await button(p,'刷新'));d=await ready(p,s=>s.workOrder&&s.workOrder.id===ctx.work_order_id,'refresh same assigned order');assert.equal(d.workOrder.status,'ASSIGNED');await snap(p,'04-assignment-refresh');
 home=await login();await tap(await button(home,'我的工单'));p=await current('pages/work-orders/list');await ready(p,s=>s.items.some(w=>w.id===ctx.work_order_id),'relogin list');let found=false;for(const x of await p.$$('.card'))if(Number(await x.attribute('data-id'))===ctx.work_order_id){await tap(x);found=true;break;}assert(found);
 p=await current('pages/work-orders/detail');d=await ready(p,s=>s.workOrder&&s.workOrder.id===ctx.work_order_id,'relogin same order');assert.equal(d.workOrder.status,'ASSIGNED');await snap(p,'05-assignment-relogin');
 assert.equal(e.requests.filter(r=>r.method==='POST').length,1);assert.equal(e.requests.find(r=>r.method==='POST').status,200);
 const hash=crypto.createHash('sha256').update(fs.readFileSync(path.join(ROOT,'miniprogram/project.config.json'))).digest('hex').toUpperCase();assert.equal(hash,'342ED0B61FE9C74E7A5CE82198C8F36BD9341D5CDA83DE2C408F33256867FE1F');e.project_config_sha256=hash;e.status='PAGE_MECHANISM_PASS_PENDING_DB_AND_INDEPENDENT_REVIEW';e.boundaries='Normal SMS + controls. One text report only; no native image picker, real-photo gold set, RAG or production claim.';save();console.log(JSON.stringify({run_id:ctx.run_id,order:ctx.work_order_id,status:e.status,screenshots:e.screenshots.length}));
}
main().catch(err=>{let msg=String(err.stack||err);for(const phone of Object.values(ctx.phones))msg=msg.split(phone).join('[REDACTED_PHONE]');e.last_error=msg.replace(/eyJ[\w-]+\.[\w-]+\.[\w-]+/g,'[REDACTED_TOKEN]');save();console.error(e.last_error);process.exitCode=1;}).finally(async()=>{if(mp){if(logging)await mp.restoreWxMethod('request').catch(()=>{});await mp.disconnect();}});
