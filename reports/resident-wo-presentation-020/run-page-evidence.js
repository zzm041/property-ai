"use strict";
// Normal auth and actual read-only navigation/refresh. Never replay lifecycle actions.
const fs=require('fs'),path=require('path'),http=require('http'),assert=require('assert/strict'),crypto=require('crypto');
const ROOT=path.resolve(__dirname,'../..'),PRIVATE=path.join(ROOT,'tmp/resident-wo-presentation-020/context.json');
const ctx=JSON.parse(fs.readFileSync(PRIVATE,'utf8')),OUT=path.join(__dirname,'page-evidence.json');
const e=fs.existsSync(OUT)?JSON.parse(fs.readFileSync(OUT,'utf8')):{task_id:'RESIDENT-WO-PRESENTATION-020',run_id:ctx.run_id,code_sha:process.env.MP_CODE_SHA,steps:{},requests:[],screenshots:[]};
let mp,logging=false;const sleep=ms=>new Promise(r=>setTimeout(r,ms));
function save(){fs.writeFileSync(OUT,JSON.stringify(e,null,2));fs.writeFileSync(PRIVATE,JSON.stringify(ctx,null,2));}
async function current(fragment){for(let i=0;i<100;i++){const p=await mp.currentPage();if(p&&p.path.includes(fragment))return p;await sleep(200);}throw Error('Page timeout '+fragment);}
async function ready(p,test,label){for(let i=0;i<120;i++){const d=await p.data();if(!d.loading&&test(d))return d;if(i>4&&!d.loading&&d.error)throw Error(label+': '+d.error);await sleep(200);}throw Error('State timeout '+label);}
async function button(p,text){for(const x of await p.$$('button'))if((await x.text()).trim()===text)return x;throw Error('Missing button '+text);}
async function fill(p,placeholder,value){for(const x of await p.$$('input'))if(await x.attribute('placeholder')===placeholder){await x.input(String(value));await sleep(200);return;}throw Error('Missing input '+placeholder);}
async function tap(x){try{await x.tap();}catch(err){if(!String(err.message).includes('timeout waiting for automator response'))throw err;await sleep(700);}}
function inbox(){return new Promise((resolve,reject)=>{const body=JSON.stringify({phone:ctx.phones.resident,purpose:'login'}),r=http.request({hostname:'127.0.0.1',port:8083,path:'/api/v1/auth/sms/test-inbox',method:'POST',headers:{'Content-Type':'application/json','Content-Length':Buffer.byteLength(body)}},res=>{let raw='';res.on('data',c=>raw+=c);res.on('end',()=>{if(res.statusCode!==200)return reject(Error('SMS inbox unavailable'));resolve(JSON.parse(raw).data.code);});});r.on('error',reject);r.end(body);});}
async function login(){
 let remain=65000-(Date.now()-(ctx.last_sms_sent_at.resident||0));while(remain>0){console.log(JSON.stringify({stage:'normal-SMS-cooldown',seconds:Math.ceil(remain/1000)}));await sleep(Math.min(remain,30000));remain=65000-(Date.now()-(ctx.last_sms_sent_at.resident||0));}
 await mp.callWxMethod('clearStorageSync');await mp.callWxMethod('reLaunch',{url:'/pages/login/login'});let p=await current('pages/login/login');
 for(const x of await p.$$('.link-text'))if((await x.text()).includes('验证码登录')){await tap(x);break;}
 await ready(p,d=>d.mode==='sms','SMS mode');await fill(p,'手机号',ctx.phones.resident);await tap(await button(p,'获取验证码'));
 let d=await ready(p,s=>s.challengePrompt,'challenge'),m=d.challengePrompt.match(/(\d+)\s*\+\s*(\d+)\s*=\s*\?/);assert(m);
 await fill(p,'挑战答案',Number(m[1])+Number(m[2]));await tap(await button(p,'获取验证码'));await ready(p,s=>!s.challengePrompt&&s.cooldown>0,'SMS sent');ctx.last_sms_sent_at.resident=Date.now();save();
 await fill(p,'短信验证码',await inbox());await tap(await button(p,'登录'));p=await current('pages/home/home');d=await ready(p,s=>s.me&&s.me.user_id,'resident home');assert.equal(d.me.user_id,ctx.identities.resident);assert(!d.me.roles.includes('SYSTEM_ADMIN'));
 ctx.resident_token=await mp.callWxMethod('getStorageSync','access_token');save();return p;
}
async function list(home){await tap(await button(home,'我的工单'));const p=await current('pages/work-orders/list');await ready(p,d=>d.items.some(x=>x.id===ctx.history_order_id)&&d.items.some(x=>x.id===ctx.active_order_id),'existing orders in list');return p;}
async function open(p,id){for(const x of await p.$$('.card'))if(Number(await x.attribute('data-id'))===id){await tap(x);p=await current('pages/work-orders/detail');await ready(p,d=>d.workOrder&&d.workOrder.id===id,'authorized detail');return p;}throw Error('Missing order card');}
async function snap(p,name,scroll=0){
 await mp.callWxMethod('pageScrollTo',{scrollTop:scroll,duration:0});await sleep(300);const d=await p.data();e.steps[name]={path:p.path,items:d.items,item:d.item,workOrder:d.workOrder,statusLabel:d.statusLabel,timezoneLabel:d.timezoneLabel,assigneeLabel:d.assigneeLabel,processHistory:d.processHistory,media:d.media,logs:d.logs,rendered_cards:await Promise.all((await p.$$('.card')).map(x=>x.text())),error:d.error};save();
 const file=name+'.png';try{await mp.screenshot({path:path.join(__dirname,file)});}catch(err){if(!String(err.message).includes('timeout'))throw err;await mp.screenshot({path:path.join(__dirname,file)});}e.screenshots.push(file);save();console.log(JSON.stringify({stage:name,order:d.workOrder?.id}));
}
async function main(){
 assert(!e.complete,'Completed; no replay');assert.equal(e.code_sha,process.env.MP_CODE_SHA);
 mp=await require(process.env.MP_AUTOMATOR_MODULE).connect({wsEndpoint:process.env.MP_AUTOMATOR_WS});
 await mp.exposeFunction('recordView020',r=>{assert(r.url.startsWith('http://127.0.0.1:8083/api/v1/'));const endpoint=r.url.slice('http://127.0.0.1:8083/api/v1'.length);if(!/^\/work-orders/.test(endpoint))return;assert(!r.method||r.method==='GET','No lifecycle write authorized');e.requests.push({path:endpoint,method:r.method||'GET',status:r.status,request_id:r.id,result:r.data,received_at_utc:new Date().toISOString(),source:'real origin-forwarded wx.request'});save();});
 await mp.mockWxMethod('request',function(o){const self=this;return new Promise((resolve,reject)=>self.origin({...o,success(r){recordView020({url:o.url,method:o.method,status:r.statusCode,id:r.data.request_id,data:r.data.data});if(o.success)o.success(r);resolve(r);},fail(err){if(o.fail)o.fail(err);reject(err);}}));});logging=true;
 let home=await login(),p=await list(home);await snap(p,'01-readable-list');p=await open(p,ctx.history_order_id);
 let d=await p.data();assert.equal(d.statusLabel,'已归档');assert.equal(d.timezoneLabel,'Asia/Shanghai');assert.equal(d.workOrder.version,10);assert.equal(d.processHistory.length,2);assert(d.processHistory.every(x=>x.receipt&&x.assigneeLabel));
 await snap(p,'02-history-top');await snap(p,'03-history-cycles',450);await snap(p,'04-readable-timeline',1800);
 await tap(await button(p,'刷新'));await ready(p,s=>s.workOrder.status==='CLOSED'&&s.processHistory.length===2,'history refresh');
 await mp.callWxMethod('reLaunch',{url:'/pages/home/home'});home=await current('pages/home/home');await ready(home,s=>s.me&&s.me.user_id,'home');p=await open(await list(home),ctx.active_order_id);
 d=await p.data();assert.equal(d.statusLabel,'已派单');assert.equal(d.media.length,2);assert(d.media.every(x=>x.phaseLabel==='报修附件'&&x.kindLabel==='图片'));await snap(p,'05-assigned-media');
 home=await login();p=await open(await list(home),ctx.history_order_id);d=await p.data();assert.equal(d.statusLabel,'已归档');assert.equal(d.workOrder.version,10);assert.equal(d.processHistory.length,2);await snap(p,'06-relogin-same-history');
 const hash=crypto.createHash('sha256').update(fs.readFileSync(path.join(ROOT,'miniprogram/project.config.json'))).digest('hex').toUpperCase();assert.equal(hash,'342ED0B61FE9C74E7A5CE82198C8F36BD9341D5CDA83DE2C408F33256867FE1F');e.project_config_sha256=hash;e.complete=true;e.status='PAGE_PASS_PENDING_INDEPENDENT_REVIEW';e.boundary='Read-only existing objects via actual controls/navigation/refresh/normalSMS relogin; no lifecycle replay, no native overlays/device claim. Listtime is explicitlyUTC; detail usesPropertytimezone.';save();console.log(JSON.stringify({run_id:ctx.run_id,status:e.status,screenshots:e.screenshots.length}));
}
main().catch(err=>{let msg=String(err.stack||err);for(const phone of Object.values(ctx.phones))msg=msg.split(phone).join('[REDACTED_PHONE]');e.last_error=msg.replace(/eyJ[\w-]+\.[\w-]+\.[\w-]+/g,'[REDACTED_TOKEN]');save();console.error(e.last_error);process.exitCode=1;}).finally(async()=>{if(mp){if(logging)await mp.restoreWxMethod('request').catch(()=>{});await mp.disconnect();}});
