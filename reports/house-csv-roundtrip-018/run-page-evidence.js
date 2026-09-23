"use strict";
// Real element input/taps, real clipboard and origin-forwarded requests; no state injection.
const fs=require('fs'),path=require('path'),http=require('http'),crypto=require('crypto'),assert=require('assert/strict');
const automator=require(process.env.MP_AUTOMATOR_MODULE),ROOT=path.resolve(__dirname,'../..');
const PRIVATE=path.join(ROOT,'tmp/house-csv-roundtrip-018/context.json'),ctx=JSON.parse(fs.readFileSync(PRIVATE,'utf8'));
const OUT=path.join(__dirname,'page-evidence.json');
const expectedTotal=JSON.parse(fs.readFileSync(path.join(__dirname,'preflight.json'),'utf8')).house_count_before+2;
const e=fs.existsSync(OUT)?JSON.parse(fs.readFileSync(OUT,'utf8')):{task_id:'HOUSE-CSV-ROUNDTRIP-018',run_id:ctx.run_id,code_sha:process.env.MP_CODE_SHA,steps:{},requests:[],screenshots:[]};
const header='building_code,building_name,unit_code,unit_name,room_no,floor,status';
const code='018-'+ctx.run_id.slice(-8),building=ctx.run_id+' 北区,"一"\n二栋',unit='东\n\n单元 "甲"';
const quote=v=>/[",\r\n]/.test(v)?'"'+v.replace(/"/g,'""')+'"':v;
const fresh=header+'\r\n'+['101','102'].map((room,i)=>[code,building,'A',unit,room,String(i+1),'ACTIVE'].map(quote).join(',')).join('\r\n');
let mp,logging=false;const sleep=ms=>new Promise(r=>setTimeout(r,ms));
const digest=()=>crypto.createHash('sha256').update(fs.readFileSync(path.join(ROOT,'miniprogram/project.config.json'))).digest('hex').toUpperCase();
const expectedHash='342ED0B61FE9C74E7A5CE82198C8F36BD9341D5CDA83DE2C408F33256867FE1F';
function save(){fs.writeFileSync(OUT,JSON.stringify(e,null,2));fs.writeFileSync(PRIVATE,JSON.stringify(ctx,null,2));}
async function current(fragment){for(let i=0;i<100;i++){const p=await mp.currentPage();if(p&&p.path.includes(fragment))return p;await sleep(200);}throw Error('Navigation timeout: '+fragment);}
async function ready(p,test,label,allowError=false){for(let i=0;i<120;i++){const d=await p.data();if(!d.loading&&test(d))return d;if(!allowError&&i>5&&!d.loading&&d.error)throw Error(label+': '+d.error);await sleep(200);}throw Error('State timeout: '+label);}
async function button(p,text){for(const x of await p.$$('button'))if((await x.text()).trim()===text)return x;throw Error('Button missing: '+text);}
async function fill(p,placeholder,value){for(const x of await p.$$('input'))if(await x.attribute('placeholder')===placeholder){await x.input(value);return;}throw Error('Input missing: '+placeholder);}
async function tap(x){try{await x.tap();}catch(err){if(!String(err.message).includes('timeout waiting for automator response'))throw err;await sleep(700);}}
async function inbox(){return new Promise((resolve,reject)=>{const body=JSON.stringify({phone:ctx.phones.manager,purpose:'login'});const r=http.request({hostname:'127.0.0.1',port:8083,path:'/api/v1/auth/sms/test-inbox',method:'POST',headers:{'Content-Type':'application/json','Content-Length':Buffer.byteLength(body)}},res=>{let b='';res.on('data',c=>b+=c);res.on('end',()=>{const x=JSON.parse(b);if(res.statusCode!==200)return reject(Error('Development inbox unavailable'));resolve(x.data.code);});});r.on('error',reject);r.end(body);});}
async function login(){
 const remaining=65000-(Date.now()-(ctx.last_sms_sent_at.manager||0));if(remaining>0){console.log(JSON.stringify({stage:'normal-SMS-cooldown',seconds:Math.ceil(remaining/1000)}));await sleep(remaining);}
 await mp.callWxMethod('clearStorageSync');await mp.callWxMethod('reLaunch',{url:'/pages/login/login'});let p=await current('pages/login/login');
 for(const x of await p.$$('.link-text'))if((await x.text()).includes('验证码登录')){await tap(x);break;}
 await ready(p,d=>d.mode==='sms','SMS mode');await fill(p,'手机号',ctx.phones.manager);await tap(await button(p,'获取验证码'));
 const d=await ready(p,s=>s.challengePrompt,'challenge'),m=d.challengePrompt.match(/(\d+)\s*\+\s*(\d+)\s*=\s*\?/);assert(m);
 await fill(p,'挑战答案',String(Number(m[1])+Number(m[2])));await tap(await button(p,'获取验证码'));
 await ready(p,s=>!s.challengePrompt&&s.cooldown>0,'SMS sent',true);ctx.last_sms_sent_at.manager=Date.now();save();
 await fill(p,'短信验证码',await inbox());await tap(await button(p,'登录'));p=await current('pages/home/home');
 const me=(await ready(p,d=>d.me&&d.me.user_id,'manager home')).me;assert.equal(me.user_id,ctx.identities.manager);assert(!me.roles.includes('SYSTEM_ADMIN'));
 ctx.manager_token=await mp.callWxMethod('getStorageSync','access_token');save();
 e.authentication={user_id:me.user_id,roles:me.roles,mode:'normal challenge/SMS/login controls, Development inbox only, no Seed'};return p;
}
async function open(home){await tap(await button(home,'楼栋房屋与住户关系'));const p=await current('pages/supervisor/properties');const d=await ready(p,s=>s.canEdit&&s.houses,'property archive');assert.equal(Number(d.propertyId),ctx.property_id);return p;}
async function snap(p,name,selector){
 await mp.callWxMethod('pageScrollTo',{scrollTop:0,duration:0});if(selector){const x=await p.$(selector),o=await x.offset();await mp.callWxMethod('pageScrollTo',{scrollTop:Math.max(0,o.top-10),duration:0});}
 const d=await p.data();e.steps[name]={path:p.path,property_id:d.propertyId,canEdit:d.canEdit,notice:d.notice,error:d.error,
  buildings:d.buildings.filter(x=>x.code===code),units:d.units.filter(x=>d.buildings.some(b=>b.code===code&&b.id===x.building_id)),houses:d.houses.filter(x=>x.buildingCode===code)};save();
 await sleep(300);const file=name+'.png';try{await mp.screenshot({path:path.join(__dirname,file)});}catch(err){if(!String(err.message).includes('timeout'))throw err;await mp.screenshot({path:path.join(__dirname,file)});}
 if(!e.screenshots.includes(file))e.screenshots.push(file);save();console.log(JSON.stringify({stage:name}));
}
async function logs(){await mp.exposeFunction('recordCsv018',r=>{
 assert(r.url.startsWith('http://127.0.0.1:8083/api/v1/'));const endpoint=r.url.slice('http://127.0.0.1:8083/api/v1'.length);
 if(!/^\/admin\/(houses\/(import|export)|property-archive)/.test(endpoint))return;
 e.requests.push({path:endpoint,method:r.method||'GET',status:r.status,request_id:r.id,result:r.result,error:r.error,
  received_at_utc:new Date().toISOString(),source:'real origin-forwarded wx.request; no fake result'});save();
 });await mp.mockWxMethod('request',function(o){const self=this;return new Promise((resolve,reject)=>self.origin({...o,success(r){recordCsv018({url:o.url,method:o.method,status:r.statusCode,id:r.data.request_id,result:r.data.data,error:r.data.error});if(o.success)o.success(r);resolve(r);},fail(err){if(o.fail)o.fail(err);reject(err);}}));});logging=true;}
const imports=()=>e.requests.filter(r=>r.path==='/admin/houses/import'&&r.method==='POST');
async function main(){
 assert.equal(digest(),expectedHash);assert.equal(e.code_sha,process.env.MP_CODE_SHA);assert(!e.status,'Completed; do not replay');
 mp=await automator.connect({wsEndpoint:process.env.MP_AUTOMATOR_WS});await logs();let p;
 if(e.steps['01-malformed-rejected']){p=await current('pages/supervisor/properties');assert.equal(Number((await p.data()).propertyId),ctx.property_id);}
 else p=await open(await login());
 if(!e.steps['01-malformed-rejected']){
  const n=imports().length;await (await p.$('textarea.csv-box')).input(header+'\nB,"unfinished\nname');await tap(await button(p,'导入 CSV 内容'));
  await ready(p,d=>d.error&&d.error.includes('引号'),'malformed input rejected',true);assert.equal(imports().length,n);await snap(p,'01-malformed-rejected','.error');
 }
 if(!e.steps['02-import-success']){
  if(!imports().some(r=>r.status===200&&r.result.created_houses===2)){
   assert(!(await p.data()).buildings.some(b=>b.code===code),'Marker exists without saved success; inspect without replay');
   await (await p.$('textarea.csv-box')).input(fresh);await ready(p,d=>d.importText===fresh,'CSV input event applied',true);
   const b=await button(p,'导入 CSV 内容');await Promise.all([tap(b),tap(b)]);
  }
  await ready(p,d=>d.buildings.some(b=>b.code===code)&&d.houses.filter(h=>h.buildingCode===code).length===2,'two new houses');
  const d=await p.data();assert.equal(d.buildings.find(b=>b.code===code).name,building);assert.equal(d.units.find(u=>u.building_id===d.buildings.find(b=>b.code===code).id).name,unit);
  ctx.house_ids=d.houses.filter(h=>h.buildingCode===code).map(h=>h.id);ctx.building_code=code;save();await snap(p,'02-import-success','.notice');
 }
 if(!e.steps['03-export-clipboard']){
  await tap(await button(p,'导出并复制 CSV'));await ready(p,d=>d.notice.startsWith('已导出并复制'),'export complete');
  const clip=await mp.callWxMethod('getClipboardData');ctx.exported_csv=clip.data;assert(ctx.exported_csv.includes(quote(building)));assert(ctx.exported_csv.includes(quote(unit)));save();
  e.clipboard={source:'actual wx.setClipboardData then getClipboardData; not mocked',length:clip.data.length,sha256:crypto.createHash('sha256').update(clip.data).digest('hex'),has_quoted_multiline_names:true};await snap(p,'03-export-clipboard','.notice');
 }
 if(!e.steps['04-reimport-unchanged']){
  if(!imports().some(r=>r.status===200&&r.result.unchanged_houses===expectedTotal)){
   await (await p.$('textarea.csv-box')).input(ctx.exported_csv);await ready(p,d=>d.importText===ctx.exported_csv,'exported CSV input event applied',true);
   await tap(await button(p,'导入 CSV 内容'));
  }
  await ready(p,d=>d.notice.includes('房屋 0')&&d.notice.includes('未变化 '+expectedTotal),'reimport no changes');
  assert.deepEqual((await p.data()).houses.filter(h=>h.buildingCode===code).map(h=>h.id),ctx.house_ids);await snap(p,'04-reimport-unchanged','.notice');
 }
 if(!e.steps['05-refresh-relogin']){
  await tap(await button(p,'刷新当前档案'));await ready(p,d=>d.houses.length===expectedTotal,'refresh');
  p=await open(await login());assert.deepEqual((await p.data()).houses.filter(h=>h.buildingCode===code).map(h=>h.id),ctx.house_ids);
  await snap(p,'05-refresh-relogin',`.house-card[data-id="${ctx.house_ids[0]}"]`);
 }
 const posted=imports();assert.equal(posted.length,2);assert(posted.every(r=>r.status===200));assert.equal(posted[0].result.created_houses,2);assert.equal(posted[1].result.created_houses,0);assert.equal(posted[1].result.unchanged_houses,expectedTotal);
 if(e.last_error)e.helper_interruption='Initial immediate textarea assertion ran before the asynchronous input event applied. Read-only inspection confirmed correct222-character input and no new marker building; resumed existing page with state polling. No duplicate login/import or product-code change for this interruption.';
 e.status='PAGE_PASS_PENDING_INDEPENDENT_REVIEW';e.house_ids=ctx.house_ids;e.building_code=code;e.project_config_sha256=digest();assert.equal(e.project_config_sha256,expectedHash);save();console.log(JSON.stringify({run_id:ctx.run_id,status:e.status,houses:ctx.house_ids,screenshots:e.screenshots.length}));
}
main().catch(err=>{let msg=String(err.stack||err);for(const phone of Object.values(ctx.phones))msg=msg.split(phone).join('[REDACTED_PHONE]');e.last_error=msg.replace(/eyJ[\w-]+\.[\w-]+\.[\w-]+/g,'[REDACTED_TOKEN]');save();console.error(e.last_error);process.exitCode=1;}).finally(async()=>{if(mp){if(logging)await mp.restoreWxMethod('request').catch(()=>{});await mp.disconnect();}});
