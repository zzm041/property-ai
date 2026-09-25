"use strict";
// Real controls and origin requests; no setData/token injection/mock response.
const fs=require('fs'),path=require('path'),http=require('http'),assert=require('assert/strict');
const ROOT='D:/property-ai',PRIVATE=path.join(ROOT,'tmp/real-ai-integration-027/runtime/context.json'),OUT=path.join(__dirname,'pages.json');
let ctx=JSON.parse(fs.readFileSync(PRIVATE,'utf8'));
const cases=JSON.parse(fs.readFileSync(path.join(__dirname,'rag-cases.json'),'utf8'));
const evidence=fs.existsSync(OUT)?JSON.parse(fs.readFileSync(OUT,'utf8')):{run_id:ctx.run_id,steps:{},requests:[],screenshots:[]};
const mode=process.argv[2];let mp,logging=false;
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
function save(){fs.writeFileSync(OUT,JSON.stringify(evidence,null,2));const disk=JSON.parse(fs.readFileSync(PRIVATE,'utf8'));ctx={...disk,...ctx};fs.writeFileSync(PRIVATE,JSON.stringify(ctx,null,2));}
async function current(fragment){for(let i=0;i<100;i++){const p=await mp.currentPage();if(p&&p.path.includes(fragment))return p;await sleep(200);}throw Error('Page timeout '+fragment);}
async function ready(p,fn,label,seconds=50){for(let i=0;i<seconds*5;i++){const d=await p.data();if(!d.loading&&fn(d))return d;if(i>4&&!d.loading&&d.error)throw Error(label+': '+d.error);await sleep(200);}throw Error('State timeout '+label);}
async function button(p,label){for(const x of await p.$$('button'))if((await x.text()).trim()===label)return x;throw Error('Button missing '+label);}
async function tap(x){try{await x.tap();}catch(err){if(!String(err.message).includes('timeout waiting for automator response'))throw err;await sleep(300);}}
async function navigate(url){console.log('navigate '+url);await mp.reLaunch(url);await sleep(400);}
async function fill(p,tag,placeholder,value){for(const x of await p.$$(tag))if(await x.attribute('placeholder')===placeholder){await x.input(String(value));return;}throw Error('Input missing '+placeholder);}
function inbox(actor){return new Promise((resolve,reject)=>{const body=JSON.stringify({phone:ctx.phones[actor],purpose:'login'}),r=http.request({hostname:'127.0.0.1',port:8083,path:'/api/v1/auth/sms/test-inbox',method:'POST',headers:{'Content-Type':'application/json','Content-Length':Buffer.byteLength(body)}},res=>{let raw='';res.on('data',c=>raw+=c);res.on('end',()=>res.statusCode===200?resolve(JSON.parse(raw).data.code):reject(Error('Test inbox unavailable')));});r.on('error',reject);r.end(body);});}
async function login(actor){
 console.log('login '+actor);
 let p=await mp.currentPage();
 if(p&&!p.path.includes('pages/login/login')){if(p.path!=='pages/home/home')await navigate('/pages/home/home');p=await current('pages/home/home');console.log('read existing home');await ready(p,d=>d.me&&d.me.user_id,'existing home');console.log('logout button');await tap(await button(p,'退出登录'));await current('pages/login/login');}
 await navigate('/pages/login/login');p=await current('pages/login/login');
 for(const x of await p.$$('.link-text'))if((await x.text()).includes('验证码登录')){await tap(x);break;}
 await ready(p,d=>d.mode==='sms','SMS mode');await fill(p,'input','手机号',ctx.phones[actor]);
 const remain=65000-(Date.now()-(ctx.last_sms_sent_at[actor]||0));if(remain>0)await sleep(remain);
 await tap(await button(p,'获取验证码'));let d=await ready(p,s=>s.challengePrompt,'challenge');const m=d.challengePrompt.match(/(\d+)\s*\+\s*(\d+)\s*=\s*\?/);assert(m);
 await fill(p,'input','挑战答案',Number(m[1])+Number(m[2]));await tap(await button(p,'获取验证码'));await ready(p,s=>!s.challengePrompt&&s.cooldown>0,'SMS sent');ctx.last_sms_sent_at[actor]=Date.now();save();
 await fill(p,'input','短信验证码',await inbox(actor));await tap(await button(p,'登录'));p=await current('pages/home/home');d=await ready(p,s=>s.me&&s.me.user_id,'normal home');assert.equal(d.me.user_id,ctx.identities[actor]);assert(!d.me.roles.includes('SYSTEM_ADMIN'));
 ctx.tokens[actor]=await mp.callWxMethod('getStorageSync','access_token');ctx.page_actor=actor;evidence.steps['auth-'+actor]={user_id:d.me.user_id,roles:d.me.roles,scopes:d.me.scopes,mode:'normal SMS; Development inbox only reads code'};save();return p;
}
async function home(){await navigate('/pages/home/home');const p=await current('pages/home/home');await ready(p,d=>d.me&&d.me.user_id,'home');return p;}
async function snap(p,name){await mp.callWxMethod('pageScrollTo',{scrollTop:0,duration:0});await sleep(300);await mp.screenshot({path:path.join(__dirname,name+'.png')});evidence.screenshots.push(name+'.png');save();}
async function knowledge(){const h=await home();await tap(await button(h,'知识库维护'));const p=await current('pages/supervisor/knowledge');await ready(p,d=>d.propertyId,'knowledge scope');const d=await p.data(),index=d.properties.findIndex(x=>x.id===ctx.property_id);assert(index>=0);if(Number(d.propertyId)!==ctx.property_id){await (await p.$$('picker'))[0].trigger('change',{value:index});await ready(p,x=>Number(x.propertyId)===ctx.property_id,'selected property');}return p;}
async function select(p,id){for(const el of await p.$$('.document-card'))if(Number(await el.attribute('data-id'))===id){await tap(el);await ready(p,d=>d.selected&&d.selected.id===id,'select doc');return;}throw Error('Document not visible');}
async function docReady(p,id,version){for(let i=0;i<60;i++){await tap(await button(p,'刷新知识状态'));let d=await ready(p,s=>s.items.some(x=>x.id===id),'refresh');let doc=d.items.find(x=>x.id===id);if(doc.current_version===version){await select(p,id);return (await p.data()).selected;}if(doc.ingestion_jobs.some(x=>x.version===version&&x.status==='FAILED'))throw Error('Ingestion failed; no manual replay');await sleep(1000);}throw Error('Ingestion wait limit');}
async function createDocuments(){assert(ctx.documents.length===0,'Do not duplicate prior objects');await login('manager');let p=await knowledge();
 for(const doc of cases.documents){await fill(p,'input','文档标题',ctx.run_id+' '+doc.title);await fill(p,'textarea','粘贴本版本知识正文',doc.text);await (await p.$$('picker'))[3].trigger('change',{value:doc.visibility==='STAFF_ONLY'?1:0});
   evidence.pending='create-'+doc.key;save();await tap(await button(p,'建立并提交导入'));const d=await ready(p,s=>s.notice.includes('已建立')&&s.selected&&s.selected.title===ctx.run_id+' '+doc.title,'create document');ctx.documents.push(d.selected.id);ctx.document_keys=ctx.document_keys||{};ctx.document_keys[doc.key]=d.selected.id;save();
   const active=await docReady(p,d.selected.id,1);evidence.steps['create-'+doc.key]={document:active,path:p.path};delete evidence.pending;save();await snap(p,'doc-'+doc.key+'-active');
 }
}
async function ask(q){if(evidence.steps[q.id])return;
 if(ctx.page_actor!==q.actor)await login(q.actor);const h=await home();await tap(await button(h,'知识助手'));const p=await current('pages/assistant/assistant');await ready(p,d=>d.propertyId,'assistant scope');const d=await p.data(),index=d.properties.findIndex(x=>x.id===ctx.property_id);assert(index>=0);if(Number(d.propertyId)!==ctx.property_id)await (await p.$('picker')).trigger('change',{value:index});
 const text=await p.$('input');await text.input(q.question);evidence.pending=q.id;save();const started=Date.now();await tap(await button(p,'提问'));const answer=await ready(p,s=>s.answerStatus,'real answer',150);
 evidence.steps[q.id]={question:q.question,actor:q.actor,path:p.path,status:answer.answerStatus,answer:answer.answer,citations:answer.citations,seconds:(Date.now()-started)/1000,expected:q.expect||q.forbid,expected_document:ctx.document_keys[q.doc],expected_version:q.version,semantic_verdict:'PENDING_MAIN_AND_INDEPENDENT_REVIEW'};delete evidence.pending;save();
 if(['Q01','Q07','Q09','Q11','N05','N06'].includes(q.id))await snap(p,q.id+'-answer');console.log(JSON.stringify({step:q.id,status:answer.answerStatus,answer:answer.answer,citations:answer.citations}));
}
async function main(){
 mp=await require('C:/Users/lmj/AppData/Local/Temp/mp-automator-npm/node_modules/miniprogram-automator').connect({wsEndpoint:'ws://127.0.0.1:9440'});
 console.log('connected');
 await mp.exposeFunction('record027',r=>{evidence.requests.push(r);save();});
 await mp.mockWxMethod('request',function(o){const self=this;if(!o.url.startsWith('http://127.0.0.1:8083/api/v1/'))throw Error('Outside AUTH001');if(o.url.includes('/work-orders'))throw Error('AUTO blocked: no work order requests');return new Promise((resolve,reject)=>self.origin({...o,success(r){if(o.url.includes('/knowledge-documents')||o.url.includes('/assistant/query'))record027({path:o.url.split('/api/v1')[1],method:o.method||'GET',status:r.statusCode,request_id:r.data.request_id,data:r.data.data||r.data.error,source:'actual origin wx.request',utc:new Date().toISOString()});if(o.success)o.success(r);resolve(r);},fail(e){if(o.fail)o.fail(e);reject(e);}}));});logging=true;
 console.log('origin observer installed');
 if(evidence.pending)throw Error('Prior step uncertain: inspect pending '+evidence.pending+'; do not replay');
 if(mode==='create')await createDocuments();
 else if(mode==='auth')await login(process.argv[3]);
 else if(mode==='questions')for(const q of cases.normal_questions.filter(x=>x.version===1))await ask(q);
 else if(mode==='resource-recheck')for(const q of cases.normal_questions.filter(x=>['Q02','Q03','Q04','Q05'].includes(x.id))){assert.equal(evidence.steps[q.id].status,'knowledge_unavailable');await ask({...q,id:q.id+'-fix1'});}
 else if(mode==='version'){
   assert(!evidence.steps.version2);await login('manager');const p=await knowledge();await select(p,ctx.document_keys.A);await fill(p,'textarea','新版本知识正文',cases.documents[0].v2);evidence.pending='version2';save();await tap(await button(p,'提交新版本导入'));await ready(p,d=>d.notice.includes('v2'),'version submitted');evidence.steps.version2=await docReady(p,ctx.document_keys.A,2);delete evidence.pending;save();await snap(p,'doc-A-v2');for(const q of cases.normal_questions.filter(x=>x.version===2))await ask(q);
 }else if(mode==='negative'){
   await ask({id:'N05',actor:'resident',question:'合成测试钥匙存放在几号柜子？',forbid:['S-27','STAFF_ONLY引用']});
   await login('manager');const p=await knowledge();await select(p,ctx.document_keys.C);evidence.pending='archive-C';save();await tap(await button(p,'归档文档'));await ready(p,d=>d.notice.includes('已归档'),'archive');evidence.steps.archive={document:ctx.document_keys.C,notice:(await p.data()).notice};delete evidence.pending;save();
   await (await p.$('switch')).trigger('change',{value:true});await ready(p,d=>d.items.some(x=>x.id===ctx.document_keys.C&&x.status==='ARCHIVED'),'archive history');await select(p,ctx.document_keys.C);evidence.steps.archived_history=(await p.data()).selected;save();await snap(p,'doc-C-archived');await ask({id:'N06',actor:'resident',question:'植物认领活动在什么地方？',forbid:['引用已归档C','无据编造地点']});
 }else throw Error('Unknown mode');
 console.log(JSON.stringify({mode,completed:true,run_id:ctx.run_id}));
}
main().catch(err=>{let msg=String(err.stack||err);for(const value of [...Object.values(ctx.phones),...Object.values(ctx.tokens)])if(value)msg=msg.split(value).join('[PRIVATE]');evidence.errors=evidence.errors||[];evidence.errors.push({mode,utc:new Date().toISOString(),error:msg});save();console.error(msg);process.exitCode=1;}).finally(async()=>{if(mp){if(logging)await mp.restoreWxMethod('request').catch(()=>{});await mp.disconnect();}});
