"use strict";
// Normal SMS login and actual navigation/refresh. Never inject auth/page state.
const fs=require('fs'),path=require('path'),http=require('http'),assert=require('assert/strict');
const ROOT='D:/property-ai',privatePath=ROOT+'/tmp/db-task-recovery-025/context.json';
const ctx=JSON.parse(fs.readFileSync(privatePath,'utf8')),out=path.join(__dirname,'page-evidence.json');
const e=fs.existsSync(out)?JSON.parse(fs.readFileSync(out,'utf8')):{run_id:ctx.run_id,steps:{},requests:[],screenshots:[]};
let mp,observing=false;const sleep=ms=>new Promise(r=>setTimeout(r,ms));
function scrub(v,key=''){if(/phone|token|authorization|credential/i.test(key))return '[redacted]';if(Array.isArray(v))return v.map(x=>scrub(x));if(v&&typeof v==='object')return Object.fromEntries(Object.entries(v).map(([k,x])=>[k,scrub(x,k)]));if(typeof v==='string'){for(const p of Object.values(ctx.phones))v=v.split(p).join('[redacted-phone]');return v.replace(/eyJ[\w-]+\.[\w-]+\.[\w-]+/g,'[redacted-token]');}return v;}
function save(){fs.writeFileSync(out,JSON.stringify(scrub(e),null,2));}
async function ready(p,check){for(let i=0;i<120;i++){const d=await p.data();if(!d.loading&&check(d))return d;await sleep(250);}throw Error('page state timeout '+p.path);}
async function current(name){for(let i=0;i<120;i++){const p=await mp.currentPage();if(p&&p.path.includes(name))return p;await sleep(250);}throw Error('navigation timeout '+name);}
async function button(p,text){for(const b of await p.$$('button'))if((await b.text()).trim()===text)return b;throw Error('missing button '+text);}
async function fill(p,placeholder,value){for(const x of await p.$$('input'))if(await x.attribute('placeholder')===placeholder){await x.input(String(value));return;}throw Error('missing input');}
function inbox(){return new Promise((resolve,reject)=>{const body=JSON.stringify({phone:ctx.phones.resident,purpose:'login'});const r=http.request({hostname:'127.0.0.1',port:8083,path:'/api/v1/auth/sms/test-inbox',method:'POST',headers:{'Content-Type':'application/json','Content-Length':Buffer.byteLength(body)}},res=>{let raw='';res.on('data',x=>raw+=x);res.on('end',()=>res.statusCode===200?resolve(JSON.parse(raw).data.code):reject(Error('inbox denied')));});r.on('error',reject);r.end(body);});}
async function login(){
 let p=await mp.currentPage();
 if(p&&p.path!=='pages/login/login'){
  await mp.callWxMethod('reLaunch',{url:'/pages/home/home'});p=await current('pages/home/home');await ready(p,d=>d.me);await(await button(p,'退出登录')).tap();await current('pages/login/login');
 }
 await mp.callWxMethod('reLaunch',{url:'/pages/login/login'});p=await current('pages/login/login');
 for(const x of await p.$$('.link-text'))if((await x.text()).includes('验证码登录')){await x.tap();break;}
 await ready(p,d=>d.mode==='sms');await fill(p,'手机号',ctx.phones.resident);await(await button(p,'获取验证码')).tap();
 let d=await ready(p,d=>d.challengePrompt),m=d.challengePrompt.match(/(\d+)\s*\+\s*(\d+)\s*=\s*\?/);assert(m);
 await fill(p,'挑战答案',Number(m[1])+Number(m[2]));await(await button(p,'获取验证码')).tap();await ready(p,d=>!d.challengePrompt&&d.cooldown>0);
 await fill(p,'短信验证码',await inbox());await(await button(p,'登录')).tap();p=await current('pages/home/home');d=await ready(p,d=>d.me&&d.me.user_id);assert.equal(d.me.user_id,ctx.identities.resident);
 e.steps.login={user_id:d.me.user_id,mode:'normal page SMS/Development OTP'};save();
}
async function view(label,name,expected){
 await mp.callWxMethod('navigateTo',{url:'/pages/work-orders/detail?id='+ctx.orders[label]});const p=await current('pages/work-orders/detail');
 let d=await ready(p,d=>d.workOrder&&d.workOrder.id===ctx.orders[label]);assert.equal(d.workOrder.status,expected);
 await(await button(p,'刷新')).tap();d=await ready(p,d=>d.workOrder&&d.workOrder.id===ctx.orders[label]);
 if(label==='persistent')assert.equal(d.recoveryFailed,true);
 const contact=await p.$('#order-contact');await ready(contact,d=>d.collected&&d.masked);assert(!(await contact.data()).phone);
 await mp.callWxMethod('pageScrollTo',{scrollTop:0,duration:0});await sleep(300);await mp.screenshot({path:path.join(__dirname,name+'.png')});
 e.screenshots.push(name+'.png');e.steps[name]={path:p.path,work_order:d.workOrder,statusLabel:d.statusLabel,recoveryFailed:d.recoveryFailed,logs:d.logs,contact:'collected/masked, no plaintext screenshot'};save();
}
async function main(){
 mp=await require('C:/Users/lmj/AppData/Local/Temp/mp-automator-npm/node_modules/miniprogram-automator').connect({wsEndpoint:'ws://127.0.0.1:9440'});
 await mp.exposeFunction('observe025',r=>{if(r.path.startsWith('/work-orders')){e.requests.push({...scrub(r),source:'real origin-forwarded wx.request',received_utc:new Date().toISOString()});save();}});
 await mp.mockWxMethod('request',function(o){const self=this;if(!o.url.startsWith('http://127.0.0.1:8083/api/v1/'))throw Error('isolated API only');return new Promise((resolve,reject)=>self.origin({...o,success(r){observe025({path:o.url.slice('http://127.0.0.1:8083/api/v1'.length),method:o.method||'GET',status:r.statusCode,data:r.data.data});if(o.success)o.success(r);resolve(r);},fail(x){if(o.fail)o.fail(x);reject(x);}}));});observing=true;
 if(process.argv[2]==='combo'){await login();await view('dispatch','01-real-db-retry-auto','ASSIGNED');await view('busy','02-busy-waiting','PENDING_ASSIGNMENT');}
 else if(process.argv[2]==='release'){await view('dispatch','03-completed-contact','CLOSED');await view('busy','04-release-auto','ASSIGNED');}
 else if(process.argv[2]==='failure'){await view('persistent','05-exhaustion-visible','PENDING_ANALYSIS');await view('commit','06-uncertain-commit-closed','CLOSED');}
 else throw Error('unknown page phase');
 console.log(JSON.stringify({phase:process.argv[2],screenshots:e.screenshots.length}));
}
main().catch(err=>{e.errors=e.errors||[];e.errors.push(scrub(String(err.stack)));save();console.error(String(err.message));process.exitCode=1;}).finally(async()=>{if(mp){if(observing)await mp.restoreWxMethod('request').catch(()=>{});await mp.disconnect();}});
