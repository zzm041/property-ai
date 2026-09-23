"use strict";
// Actual controls/navigation and real wx.request. No injected business state.
const fs = require("fs");
const path = require("path");
const http = require("http");
const crypto = require("crypto");
const assert = require("assert/strict");
const automator = require(process.env.MP_AUTOMATOR_MODULE);
const REPO = path.resolve(__dirname, "../..");
const PRIVATE = path.join(REPO, "tmp/manager-wo-cancel-017/context.json");
const ctx = JSON.parse(fs.readFileSync(PRIVATE, "utf8"));
const EVIDENCE = path.join(__dirname, "page-evidence.json");
const evidence = fs.existsSync(EVIDENCE) ? JSON.parse(fs.readFileSync(EVIDENCE, "utf8")) : {
  task_id: "MANAGER-WO-CANCEL-017", run_id: ctx.run_id,
  code_sha: process.env.MP_CODE_SHA, steps: {}, requests: [], screenshots: [],
  mode: "Actual normal SMS page authentication, controls and real AUTH001 requests; no fake business state or manual dispatch.",
};
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const hash = file => crypto.createHash("sha256").update(fs.readFileSync(file)).digest("hex").toUpperCase();
const configHash = hash(path.join(REPO, "miniprogram/project.config.json"));
let mp;
let logging = false;
function save() {
  fs.writeFileSync(EVIDENCE, JSON.stringify(evidence, null, 2));
  fs.writeFileSync(PRIVATE, JSON.stringify(ctx, null, 2));
}
async function current(fragment) {
  for (let i = 0; i < 100; i++) {
    const page = await mp.currentPage();
    if (page && page.path.includes(fragment)) return page;
    await sleep(200);
  }
  throw new Error(`Page did not reach ${fragment}`);
}
async function ready(page, predicate, label, allowError = false) {
  for (let i = 0; i < 120; i++) {
    const data = await page.data();
    if (!data.loading && predicate(data)) return data;
    if (!allowError && i > 5 && !data.loading && data.error) throw new Error(`${label}: ${data.error}`);
    await sleep(200);
  }
  throw new Error(`Page state timeout: ${label}`);
}
async function byText(page, text) {
  for (const item of await page.$$("button")) if ((await item.text()).trim() === text) return item;
  throw new Error(`Button missing: ${text}`);
}
async function attr(page, selector, key, value) {
  for (const item of await page.$$(selector)) if (String(await item.attribute(key)) === String(value)) return item;
  throw new Error(`Control missing: ${selector} ${key}=${value}`);
}
async function fill(page, placeholder, value) {
  const inputs = [...await page.$$("input"), ...await page.$$("textarea")];
  for (const item of inputs) if (await item.attribute("placeholder") === placeholder) { await item.input(String(value)); return; }
  throw new Error(`Input missing: ${placeholder}`);
}
async function tap(item) {
  try { await item.tap(); }
  catch (error) {
    if (!String(error.message).includes("timeout waiting for automator response")) throw error;
    await sleep(700); // Inspect actual state; never repeat a timed-out mutation.
  }
}
function inbox(phone) {
  return new Promise((resolve, reject) => {
    const data = JSON.stringify({phone, purpose:"login"});
    const request = http.request({hostname:"127.0.0.1", port:8083, path:"/api/v1/auth/sms/test-inbox", method:"POST",
      headers:{"Content-Type":"application/json","Content-Length":Buffer.byteLength(data)}}, response => {
      let raw=""; response.on("data", b => raw += b); response.on("end", () => {
        const payload=JSON.parse(raw); if(response.statusCode !== 200 || !payload.data.code) return reject(new Error("Development SMS inbox unavailable"));
        resolve(payload.data.code);
      });
    });
    request.on("error", reject); request.end(data);
  });
}
async function login(identity) {
  const remaining = 65000 - (Date.now() - (ctx.last_sms_sent_at[identity] || 0));
  if (remaining > 0) { console.log(JSON.stringify({stage:"waiting-normal-SMS-cooldown",seconds:Math.ceil(remaining/1000)})); await sleep(remaining); }
  await mp.callWxMethod("clearStorageSync");
  await mp.callWxMethod("reLaunch", {url:"/pages/login/login"});
  let page = await current("pages/login/login");
  await tap(await attr(page, ".link-text", "data-mode", "sms").catch(async () => {
    for(const item of await page.$$(".link-text")) if ((await item.text()).includes("验证码登录")) return item;
    throw new Error("SMS mode missing");
  }));
  await ready(page, state => state.mode === "sms", "SMS mode");
  await fill(page, "手机号", ctx.phones[identity]);
  await tap(await byText(page, "获取验证码"));
  let state = await ready(page, value => Boolean(value.challengePrompt), "challenge");
  const match = state.challengePrompt.match(/(\d+)\s*\+\s*(\d+)\s*=\s*\?/);
  assert(match, "Unexpected Development challenge");
  await fill(page, "挑战答案", String(Number(match[1])+Number(match[2])));
  await tap(await byText(page, "获取验证码"));
  await ready(page, value => !value.challengePrompt && value.cooldown > 0, "SMS sent", true);
  ctx.last_sms_sent_at[identity] = Date.now(); save();
  await fill(page, "短信验证码", await inbox(ctx.phones[identity]));
  await tap(await byText(page, "登录"));
  page = await current("pages/home/home");
  state = await ready(page, value => value.me && value.me.user_id, "authenticated home");
  assert.equal(Number(state.me.user_id), Number(ctx.identities[identity]));
  assert(!state.me.roles.includes("SYSTEM_ADMIN"));
  return page;
}
async function navigate(home, url) {
  await tap(await attr(home, "button", "data-url", url));
  return current(url.replace(/^\//, ""));
}



async function snap(page,name,selector) {
 await mp.callWxMethod("pageScrollTo",{scrollTop:0,duration:0});
 if(selector){const el=typeof selector==='string'?await page.$(selector):selector;assert(el);const offset=await el.offset();await mp.callWxMethod("pageScrollTo",{scrollTop:Math.max(0,offset.top-12),duration:0});}
 const d=await page.data();
 evidence.steps[name]={path:page.path,work_order:d.workOrder||null,item:d.item||null,logs:d.logs||null,
  selectedId:d.selectedId,selectedStatus:d.selectedStatus,selectedVersion:d.selectedVersion,queueView:d.queueView,
  canCancel:d.canCancel,cancelConfirm:d.cancelConfirm,cancelReason:d.cancelReason,success:d.success,
  queue_items:d.items||null,error:d.error};
 const file=name+".png";await sleep(400);
 try { await mp.screenshot({path:path.join(__dirname,file)}); }
 catch(error) { if(!String(error.message).includes('timeout'))throw error;await mp.screenshot({path:path.join(__dirname,file)}); }
 if(!evidence.screenshots.includes(file))evidence.screenshots.push(file);save();console.log(JSON.stringify({stage:name,order:ctx.page_order_id}));
}
async function logs() {
 await mp.exposeFunction("recordCancel017",data=>{
  assert(data.url.startsWith("http://127.0.0.1:8083/api/v1/"));
  const relative=data.url.slice("http://127.0.0.1:8083/api/v1".length);
  if(!/^\/(work-orders|supervisor\/work-orders|staff\/work-orders|supervisor\/staff\/daily)/.test(relative))return;
  if(relative==="/work-orders"&&data.method==="POST"&&data.status===200&&data.result.id){
   assert(!ctx.page_order_id||ctx.page_order_id===data.result.id);ctx.page_order_id=data.result.id;
  }
  evidence.requests.push({path:relative,method:data.method||"GET",status:data.status,request_id:data.requestId,result:data.result,
    received_at_utc:new Date().toISOString(),source:"real origin-forwarded wx.request; no fake response"});
  save();
 });
 await mp.mockWxMethod("request",function(options){const self=this;return new Promise((resolve,reject)=>self.origin({...options,
  success(response){recordCancel017({url:options.url,method:options.method,status:response.statusCode,requestId:response.data.request_id,result:response.data.data});if(options.success)options.success(response);resolve(response);},
  fail(error){if(options.fail)options.fail(error);reject(error);}
 }));});logging=true;
}
async function openResidentOrder(home){
 const list=await navigate(home,"/pages/work-orders/list");await ready(list,d=>d.items.some(r=>(r.work_order||r).id===ctx.page_order_id),"resident order list");
 await tap(await attr(list,".card","data-id",ctx.page_order_id));const page=await current("pages/work-orders/detail");
 await ready(page,d=>d.workOrder&&d.workOrder.id===ctx.page_order_id,"resident order detail");return page;
}
async function managerQueue(){
 const home=await login("manager"),page=await navigate(home,"/pages/supervisor/queue");
 await ready(page,d=>!d.loading,"queue");
 await tap(await byText(page,"未开工工单"));
 await ready(page,d=>d.queueView==="unstarted"&&!d.loading,"unstarted queue");return page;
}
async function main(){
 assert.equal(evidence.code_sha,process.env.MP_CODE_SHA);
 assert(!evidence.status,"Already completed; no replay");
 mp=await automator.connect({wsEndpoint:process.env.MP_AUTOMATOR_WS});await logs();
 if(evidence.steps["05-manager-relogin"]&&!evidence.screenshots.includes("05-manager-relogin.png")){
  const page=await current("pages/supervisor/queue"),d=await page.data();
  assert.equal(d.queueView,"unstarted");assert(!d.items.some(r=>r.id===ctx.page_order_id));
  await snap(page,"05-manager-relogin");
  evidence.helper_interruption="Initial final screenshot timed out after manager relogin/state and single successful cancel were already saved. Read-only page inspection confirmed unstarted queue; resumed only current screenshot, no login or business mutation repeated.";
 }
 if(!evidence.steps["01-auto-assigned"]){
  const home=await login("resident");let page;
  if(!ctx.page_order_id){
   page=await navigate(home,"/pages/work-orders/create");
   await fill(page,"房屋 ID",ctx.house_id);
   await fill(page,"报修说明",ctx.run_id+" 页面撤销验证：客厅灯不亮，开关接触不良。");
   await tap(await byText(page,"提交报修"));page=await current("pages/work-orders/detail");
   const d=await ready(page,s=>s.workOrder&&s.workOrder.id,"created order");
   ctx.page_order_id=d.workOrder.id;save();
  }else page=await openResidentOrder(home);
  const state=await ready(page,d=>d.workOrder.status==="ASSIGNED","independent Worker AUTO assignment");
  const assignment=state.item.assignments.at(-1);assert.equal(assignment.reason,"AUTO_DISPATCH");assert.equal(assignment.assigned_by_user_id,null);
  ctx.page_staff=Object.keys(ctx.employees).find(k=>ctx.employees[k]===assignment.employee_id);assert(ctx.page_staff);save();
  await snap(page,"01-auto-assigned");
 }
 if(!evidence.steps["03-manager-cancel-success"]){
  const page=await managerQueue();let d=await page.data();
  if(d.items.some(r=>r.id===ctx.page_order_id)){
   await tap(await attr(page,".card","data-id",ctx.page_order_id));
   await ready(page,s=>s.selectedId===ctx.page_order_id&&s.canCancel,"selected order");
   await fill(page,"撤销原因（可选，保留在工单历史）",ctx.run_id+" 管理核实为重复报修，尚未开工。");
   await tap(await byText(page,"撤销此工单"));
   await ready(page,s=>s.cancelConfirm,"inline confirmation");
   await snap(page,"02-manager-confirmation",await byText(page,"确认撤销未开工工单"));
   const button=await byText(page,"确认撤销未开工工单");
   await Promise.all([tap(button),tap(button)]);
   await ready(page,s=>Boolean(s.success)&&!s.items.some(r=>r.id===ctx.page_order_id),"cancellation committed");
  }else{
   assert(evidence.requests.some(r=>r.method==="POST"&&r.path==="/work-orders/"+ctx.page_order_id+"/cancel"&&r.status===200),"Missing order without evidence: inspect, never recreate");
  }
  await snap(page,"03-manager-cancel-success");
 }
 if(!evidence.steps["04-resident-cancel-history"]){
  const home=await login("resident"),page=await openResidentOrder(home);
  await ready(page,d=>d.workOrder.status==="CANCELLED","resident cancelled status");
  assert((await page.data()).logs.some(r=>r.event_code==="CANCEL"&&r.reason.includes(ctx.run_id)));
  await tap(await byText(page,"刷新"));await ready(page,d=>d.workOrder.status==="CANCELLED","refresh retains cancel");
  await snap(page,"04-resident-cancel-history");
 }
 if(!evidence.steps["05-manager-relogin"]){
  const page=await managerQueue();assert(!(await page.data()).items.some(r=>r.id===ctx.page_order_id));
  await snap(page,"05-manager-relogin");
 }
 const cancelRows=evidence.requests.filter(r=>r.method==="POST"&&r.path==="/work-orders/"+ctx.page_order_id+"/cancel");
 assert.equal(cancelRows.length,1);assert.equal(cancelRows[0].status,200);
 evidence.status="PAGE_PASS_PENDING_INDEPENDENT_REVIEW";
 evidence.order_id=ctx.page_order_id;evidence.staff_identity=ctx.page_staff;
 evidence.limits=["Actual UI create/AUTO/cancel/refresh/relogin; no manual analyze/assign, no fake business response.",
  "Fixture credentials are normal Development SMS; no real SMS/WeChat/device/Production claim.",
  "Unit simulations and API negatives remain separate from these actual controls."];
 assert.equal(hash(path.join(REPO,"miniprogram/project.config.json")),configHash);evidence.project_config_sha256=configHash;save();
 console.log(JSON.stringify({run_id:ctx.run_id,order:ctx.page_order_id,status:evidence.status,screenshots:evidence.screenshots.length}));
}
main().catch(error=>{let m=String(error.stack||error);for(const p of Object.values(ctx.phones))m=m.split(p).join("[REDACTED_PHONE]");
 evidence.last_error=m.replace(/eyJ[\w-]+\.[\w-]+\.[\w-]+/g,"[REDACTED_TOKEN]");save();console.error(evidence.last_error);process.exitCode=1;
}).finally(async()=>{if(mp){if(logging)await mp.restoreWxMethod("request").catch(()=>{});await mp.disconnect();}});
