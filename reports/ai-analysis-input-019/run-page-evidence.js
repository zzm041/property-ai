"use strict";
// Normal auth/page controls only. Native file selection is not simulated.
const fs=require('fs'),path=require('path'),http=require('http'),assert=require('assert/strict'),crypto=require('crypto');
const ROOT=path.resolve(__dirname,'../..'),PRIVATE=path.join(ROOT,'tmp/ai-analysis-input-019/context.json');
const ctx=JSON.parse(fs.readFileSync(PRIVATE,'utf8')),OUT=path.join(__dirname,'page-evidence.json');
const e=fs.existsSync(OUT)?JSON.parse(fs.readFileSync(OUT,'utf8')):{task_id:'AI-ANALYSIS-INPUT-019',run_id:ctx.run_id,code_sha:process.env.MP_CODE_SHA,steps:{},requests:[],screenshots:[],completed_phases:[]};
const phase=process.argv[2]||'create',sleep=ms=>new Promise(r=>setTimeout(r,ms));let mp,logging=false;
function save(){fs.writeFileSync(OUT,JSON.stringify(e,null,2));fs.writeFileSync(PRIVATE,JSON.stringify(ctx,null,2));}
async function current(fragment){for(let i=0;i<120;i++){const p=await mp.currentPage();if(p&&p.path.includes(fragment))return p;await sleep(200);}throw Error('Page timeout: '+fragment);}
async function ready(p,test,label){for(let i=0;i<150;i++){const d=await p.data();if(!d.loading&&test(d))return d;if(i>5&&!d.loading&&d.error)throw Error(label+': '+d.error);await sleep(200);}throw Error('State timeout: '+label);}
async function button(p,text){for(const x of await p.$$('button'))if((await x.text()).trim()===text)return x;throw Error('Missing button: '+text);}
async function fill(p,placeholder,value){for(const x of [...await p.$$('input'),...await p.$$('textarea')])if(await x.attribute('placeholder')===placeholder){await x.input(String(value));await sleep(200);return;}throw Error('Missing input: '+placeholder);}
async function tap(x){try{await x.tap();}catch(err){if(!String(err.message).includes('timeout waiting for automator response'))throw err;await sleep(700);}}
async function inbox(){return new Promise((resolve,reject)=>{const body=JSON.stringify({phone:ctx.phones.resident,purpose:'login'}),r=http.request({hostname:'127.0.0.1',port:8083,path:'/api/v1/auth/sms/test-inbox',method:'POST',headers:{'Content-Type':'application/json','Content-Length':Buffer.byteLength(body)}},res=>{let b='';res.on('data',c=>b+=c);res.on('end',()=>{const x=JSON.parse(b);if(res.statusCode!==200)return reject(Error('SMS inbox unavailable'));resolve(x.data.code);});});r.on('error',reject);r.end(body);});}
async function login(){
 const remain=65000-(Date.now()-(ctx.last_sms_sent_at.resident||0));if(remain>0){console.log(JSON.stringify({stage:'normal-SMS-cooldown',seconds:Math.ceil(remain/1000)}));await sleep(remain);}
 await mp.callWxMethod('clearStorageSync');await mp.callWxMethod('reLaunch',{url:'/pages/login/login'});let p=await current('pages/login/login');
 for(const x of await p.$$('.link-text'))if((await x.text()).includes('验证码登录')){await tap(x);break;}
 await ready(p,d=>d.mode==='sms','SMS mode');await fill(p,'手机号',ctx.phones.resident);await tap(await button(p,'获取验证码'));
 let d=await ready(p,s=>s.challengePrompt,'challenge'),m=d.challengePrompt.match(/(\d+)\s*\+\s*(\d+)\s*=\s*\?/);assert(m);
 await fill(p,'挑战答案',Number(m[1])+Number(m[2]));await tap(await button(p,'获取验证码'));await ready(p,s=>!s.challengePrompt&&s.cooldown>0,'SMS sent');
 ctx.last_sms_sent_at.resident=Date.now();save();await fill(p,'短信验证码',await inbox());await tap(await button(p,'登录'));
 p=await current('pages/home/home');d=await ready(p,s=>s.me&&s.me.user_id,'resident home');assert.equal(d.me.user_id,ctx.identities.resident);assert(!d.me.roles.includes('SYSTEM_ADMIN'));
 ctx.resident_token=await mp.callWxMethod('getStorageSync','access_token');ctx.resident_refresh=await mp.callWxMethod('getStorageSync','refresh_token');save();return p;
}
async function snap(p,name){
 await mp.callWxMethod('pageScrollTo',{scrollTop:0,duration:0});const d=await p.data();e.steps[name]={path:p.path,work_order:d.workOrder,autoDispatched:d.autoDispatched,assignments:d.item?.assignments,media:d.media,logs:d.logs,error:d.error};save();
 await sleep(300);const file=name+'.png';try{await mp.screenshot({path:path.join(__dirname,file)});}catch(err){if(!String(err.message).includes('timeout'))throw err;await mp.screenshot({path:path.join(__dirname,file)});}
 if(!e.screenshots.includes(file))e.screenshots.push(file);save();console.log(JSON.stringify({stage:name,order:d.workOrder?.id}));
}
async function logs(){
 await mp.exposeFunction('recordAi019',r=>{assert(r.url.startsWith('http://127.0.0.1:8083/api/v1/'));const endpoint=r.url.slice('http://127.0.0.1:8083/api/v1'.length);if(!/^\/work-orders/.test(endpoint))return;
  if(endpoint==='/work-orders'&&r.method==='POST'&&r.status===200){assert(!ctx.page_order_id||ctx.page_order_id===r.data.id);ctx.page_order_id=r.data.id;}
  e.requests.push({path:endpoint,method:r.method||'GET',status:r.status,request_id:r.id,result:r.data,received_at_utc:new Date().toISOString(),source:'real origin-forwarded wx.request, no injected success'});save();
 });
 await mp.mockWxMethod('request',function(o){const self=this;return new Promise((resolve,reject)=>self.origin({...o,success(r){recordAi019({url:o.url,method:o.method,status:r.statusCode,id:r.data.request_id,data:r.data.data});if(o.success)o.success(r);resolve(r);},fail(err){if(o.fail)o.fail(err);reject(err);}}));});logging=true;
}
async function openOrder(home,id){await tap(await button(home,'我的工单'));const p=await current('pages/work-orders/list');await ready(p,d=>d.items.some(r=>(r.work_order||r).id===id),'own list order');
 for(const x of await p.$$('.card'))if(Number(await x.attribute('data-id'))===id){await tap(x);return current('pages/work-orders/detail');}throw Error('Own order card missing');}
async function main(){
 assert.equal(e.code_sha,process.env.MP_CODE_SHA);assert(!e.completed_phases.includes(phase),'Phase already completed; no replay');
 mp=await require(process.env.MP_AUTOMATOR_MODULE).connect({wsEndpoint:process.env.MP_AUTOMATOR_WS});await logs();
 let home,p;
 if(phase==='create'){
  if(ctx.page_order_id){p=await current('pages/work-orders/detail');assert.equal((await p.data()).workOrder.id,ctx.page_order_id);}
  else{home=await login();await tap(await button(home,'报修'));p=await current('pages/work-orders/create');await fill(p,'房屋 ID',ctx.house_id);await fill(p,'报修说明',ctx.run_id+' 页面验证：客厅电灯闪烁，请检修照明线路。');await ready(p,d=>d.text.includes(ctx.run_id)&&Number(d.houseId)===ctx.house_id,'repair form');await tap(await button(p,'提交报修'));p=await current('pages/work-orders/detail');}
  let d=await ready(p,s=>s.workOrder&&s.workOrder.status==='ASSIGNED'&&s.autoDispatched,'independentWorker AUTO');ctx.page_order_id=d.workOrder.id;save();
  assert(d.item.assignments.at(-1).reason==='AUTO_DISPATCH'&&d.item.assignments.at(-1).assigned_by_user_id==null);await snap(p,'01-page-auto-assigned');
  await tap(await button(p,'刷新'));await ready(p,s=>s.workOrder.status==='ASSIGNED','refresh');await snap(p,'02-page-refresh');
 }else if(phase==='observe-api'){
  assert(ctx.api_order_id,'API phase must first save its created object');home=await login();p=await openOrder(home,ctx.api_order_id);
  await ready(p,d=>d.workOrder&&d.workOrder.id===ctx.api_order_id&&d.autoDispatched&&d.media.length===2,'same API-created two-image order');
  await snap(p,'03-api-created-images-in-page');await tap(await button(p,'刷新'));await ready(p,d=>d.media.length===2,'media refresh');await snap(p,'04-api-object-refresh');
 }else throw Error('Unknown phase');
 e.completed_phases.push(phase);e.status=e.completed_phases.length===2?'PAGE_PASS_PENDING_INDEPENDENT_REVIEW':'PAGE_CREATE_PASS_PENDING_API_MEDIA_PHASE';
 e.boundaries='Text order was created through normal page. Multi-image order is API-created then actually navigated/read/refreshed on page; native chooser is NOT exercised or mocked. Actual independentWorker usesDevelopment models, not real image-understanding quality.';
 const hash=crypto.createHash('sha256').update(fs.readFileSync(path.join(ROOT,'miniprogram/project.config.json'))).digest('hex').toUpperCase();assert.equal(hash,'342ED0B61FE9C74E7A5CE82198C8F36BD9341D5CDA83DE2C408F33256867FE1F');e.project_config_sha256=hash;save();console.log(JSON.stringify({phase,run_id:ctx.run_id,page_order:ctx.page_order_id,status:e.status}));
}
main().catch(err=>{let m=String(err.stack||err);for(const phone of Object.values(ctx.phones))m=m.split(phone).join('[REDACTED_PHONE]');e.last_error=m.replace(/eyJ[\w-]+\.[\w-]+\.[\w-]+/g,'[REDACTED_TOKEN]');save();console.error(e.last_error);process.exitCode=1;}).finally(async()=>{if(mp){if(logging)await mp.restoreWxMethod('request').catch(()=>{});await mp.disconnect();}});
