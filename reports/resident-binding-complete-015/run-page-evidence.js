"use strict";
// Actual controls/navigation and real wx.request. No injected business state.
const fs = require("fs");
const path = require("path");
const http = require("http");
const crypto = require("crypto");
const assert = require("assert/strict");
const automator = require(process.env.MP_AUTOMATOR_MODULE);
const REPO = path.resolve(__dirname, "../..");
const PRIVATE = path.join(REPO, "tmp/resident-binding-complete-015/context.json");
const ctx = JSON.parse(fs.readFileSync(PRIVATE, "utf8"));
const EVIDENCE = path.join(__dirname, "page-evidence.json");
const evidence = fs.existsSync(EVIDENCE) ? JSON.parse(fs.readFileSync(EVIDENCE, "utf8")) : {
  task_id: "RESIDENT-BINDING-COMPLETE-015", run_id: ctx.run_id,
  code_sha: process.env.MP_CODE_SHA, steps: {}, requests: [], screenshots: [],
  mode: "WeChat DevTools real controls, Development SMS, real AUTH001 API/DB; picker change uses supported automator trigger, not native picker wheel/physical-device acceptance",
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

async function snapshot(page,name,selector) {
  console.log(JSON.stringify({stage:"capture",name}));
  await mp.callWxMethod("pageScrollTo",{scrollTop:0,duration:0});
  if(selector) {const element=await page.$(selector);assert(element);const offset=await element.offset();await mp.callWxMethod("pageScrollTo",{scrollTop:Math.max(0,offset.top-10),duration:0});}
  const data=await page.data();
  evidence.steps[name]={items:data.items,requests:data.requests,error:data.error,selectedHouseId:data.selectedHouseId,
    relationIndex:data.relationIndex,relationNames:data.relationNames,rejectReason:data.rejectReason,propertyId:data.propertyId,tenantId:data.tenantId};
  await sleep(450);
  const file=name+".png";
  try { await mp.screenshot({path:path.join(__dirname,file)}); }
  catch(error) { if(!String(error.message).includes("timeout"))throw error;await sleep(500);await mp.screenshot({path:path.join(__dirname,file)}); }
  if(!evidence.screenshots.includes(file))evidence.screenshots.push(file);save();return data;
}
async function logRequests() {
  await mp.exposeFunction("recordBind015", record=>{
    if(!record.url.startsWith("http://127.0.0.1:8083/api/v1/"))throw Error("Wrong environment");
    const relative=record.url.replace("http://127.0.0.1:8083/api/v1","");
    if(!/^\/(resident-bind-requests|admin\/resident-bind-requests|me\/houses|properties|buildings|units)/.test(relative))return;
    evidence.requests.push({path:relative,method:record.method||"GET",status:record.status,
      request_id:record.requestId,request:record.body,response:record.result,
      received_at_utc:new Date().toISOString(),source:"real wx.request; origin forwarded unchanged"});
    if(relative==="/resident-bind-requests" && record.status===200) {
      ctx.page_requests=ctx.page_requests||[];
      if(!ctx.page_requests.includes(record.result.id))ctx.page_requests.push(record.result.id);
    }
    save();
  });
  await mp.mockWxMethod("request",function(options){const self=this;return new Promise((resolve,reject)=>self.origin({...options,
    success(response){const binding=options.url.includes("resident-bind-requests");
      recordBind015({url:options.url,method:options.method,status:response.statusCode,requestId:response.data&&response.data.request_id,
        body:binding?options.data:undefined,result:binding?response.data.data:undefined});
      if(options.success)options.success(response);resolve(response);},
    fail(error){if(options.fail)options.fail(error);reject(error);}
  }));});logging=true;
}
const originalLogin=login;
let lastLoginAt={};
async function normalLogin(actor) {
  const wait=65000-(Date.now()-(lastLoginAt[actor]||0));
  if(wait>0){console.log(JSON.stringify({stage:"normal SMS cooldown",actor,wait_ms:wait}));await sleep(wait);}
  const home=await originalLogin(actor);lastLoginAt[actor]=Date.now();return home;
}
async function houses() {
  const currentPage=await mp.currentPage();
  if(currentPage.path==="pages/houses/houses" && await mp.evaluate(()=>getApp().globalData.me.user_id)===ctx.identities.resident) {
    await tap(await byText(currentPage,"刷新"));await ready(currentPage,s=>!s.catalogLoading,"resumed house page");return currentPage;
  }
  const page=await navigate(await normalLogin("resident"),"/pages/houses/houses");
  await ready(page,s=>!s.catalogLoading&&s.houses.length,"house catalog");return page;
}
async function choose(page) {
  const data=await page.data(),index=data.houses.findIndex(h=>h.id===ctx.house_id);assert(index>=0);
  await (await page.$("#house-picker")).trigger("change",{value:index});
  await ready(page,s=>s.selectedHouseId===ctx.house_id,"selected house");
  for(let i=0;i<4;i++){
    await(await page.$("#relation-picker")).trigger("change",{value:i});
    await ready(page,s=>s.relationIndex===i,"selected relationship");
  }
  await(await page.$("#relation-picker")).trigger("change",{value:1});
  await ready(page,s=>s.relationIndex===1,"tenant relationship");
  assert.equal((await page.data()).selectedHouseId,ctx.house_id);
}
async function managerPage() {
  const currentPage=await mp.currentPage();
  if(currentPage.path==="pages/supervisor/bindings" && await mp.evaluate(()=>getApp().globalData.me.user_id)===ctx.identities.manager) return currentPage;
  const page=await navigate(await normalLogin("manager"),"/pages/supervisor/bindings");
  await ready(page,s=>s.items,"manager pending list");
  assert.equal(Number((await page.data()).propertyId),ctx.property_id);return page;
}
async function action(page,id,text) {
  for(const el of await page.$$("button"))if(Number(await el.attribute("data-id"))===id&&(await el.text()).trim()===text)return el;
  throw Error("review control missing");
}
async function main() {
  assert(process.env.MP_CODE_SHA&&process.env.MP_AUTOMATOR_WS);
  assert.equal(evidence.code_sha,process.env.MP_CODE_SHA);
  mp=await automator.connect({wsEndpoint:process.env.MP_AUTOMATOR_WS});await logRequests();
  if(!evidence.steps["01-tenant-pending"]) {
    const page=await houses();await choose(page);await snapshot(page,"00-relationship-selection","#relation-picker");
    const before=await page.data();
    const pending=before.requests.find(r=>r.house_id===ctx.house_id&&r.status==="PENDING");
    if(!pending)await tap(await byText(page,"提交绑定申请"));
    const data=await ready(page,s=>s.requests.some(r=>r.house_id===ctx.house_id&&r.status==="PENDING"),"tenant pending");
    const request=data.requests.find(r=>r.house_id===ctx.house_id&&r.status==="PENDING");assert.equal(request.relation_type,"TENANT");
    ctx.first_request_id=request.id;save();await snapshot(page,"01-tenant-pending","#bind-request-"+request.id);
  }
  ctx.reject_reason=ctx.reject_reason||ctx.run_id+" 请补充租赁授权证明后重新申请";save();
  if(!evidence.steps["03-manager-rejected"]) {
    const page=await managerPage();
    if((await page.data()).items.some(r=>r.id===ctx.first_request_id)){
      if(!(await page.data()).error.includes("1至500")) await tap(await action(page,ctx.first_request_id,"拒绝"));
      await ready(page,s=>s.error.includes("1至500"),"empty reason blocked",true);await snapshot(page,"02-empty-review-reason",".error");
      await fill(page,"驳回原因（仅拒绝时必填，住户可见）",ctx.reject_reason);
      await tap(await action(page,ctx.first_request_id,"拒绝"));
      await ready(page,s=>!s.items.some(r=>r.id===ctx.first_request_id),"rejected");
    }
    await snapshot(page,"03-manager-rejected");
  }
  if(!evidence.steps["05-reapplication-pending"]) {
    const page=await houses();
    const rejected=(await page.data()).requests.find(r=>r.id===ctx.first_request_id);
    assert.equal(rejected.status,"REJECTED");assert.equal(rejected.review_reason,ctx.reject_reason);
    await snapshot(page,"04-resident-rejection-reason","#bind-request-"+ctx.first_request_id);
    await choose(page);
    if(!(await page.data()).requests.some(r=>r.house_id===ctx.house_id&&r.status==="PENDING"))
      await tap(await byText(page,"提交绑定申请"));
    const data=await ready(page,s=>s.requests.some(r=>r.house_id===ctx.house_id&&r.status==="PENDING"),"new application");
    ctx.second_request_id=data.requests.find(r=>r.house_id===ctx.house_id&&r.status==="PENDING").id;
    assert.notEqual(ctx.second_request_id,ctx.first_request_id);save();
    await snapshot(page,"05-reapplication-pending","#bind-request-"+ctx.second_request_id);
  }
  if(!evidence.steps["06-manager-approved"]) {
    const page=await managerPage();
    if((await page.data()).items.some(r=>r.id===ctx.second_request_id)) {
      await tap(await action(page,ctx.second_request_id,"通过"));
      await ready(page,s=>!s.items.some(r=>r.id===ctx.second_request_id),"approved");
    }
    await snapshot(page,"06-manager-approved");
  }
  for(const [name,refresh] of [["07-active-relation-refresh",true],["08-active-relation-relogin",false]]) {
    if(evidence.steps[name])continue;
    const page=refresh?await houses():await navigate(await normalLogin("resident"),"/pages/houses/houses");
    await ready(page,s=>!s.catalogLoading&&s.items.some(r=>r.house_id===ctx.house_id),"active relation");
    if(refresh){await tap(await byText(page,"刷新"));await ready(page,s=>!s.catalogLoading,"refreshed");}
    const state=await page.data();
    const active=state.items.find(r=>r.house_id===ctx.house_id);assert(active);assert.equal(active.relation_type,"TENANT");
    assert.equal(state.requests.find(r=>r.id===ctx.first_request_id).review_reason,ctx.reject_reason);
    assert.equal(state.requests.find(r=>r.id===ctx.second_request_id).status,"APPROVED");
    await snapshot(page,name);
  }
  evidence.status="PAGE_PASS_PENDING_INDEPENDENT_REVIEW";
  evidence.limits=["Picker values were selected using the supported automator element change event, not native wheel interaction.",
    "Normal page controls and actual wx.request were used; no frontend business-state injection or fabricated successful responses.",
    "Development SMS inbox supplies synthetic one-time codes. No Seed login, real SMS provider, physical device or production acceptance.",
    "Four relation choices were exercised on the control; the persisted UI workflow uses TENANT. Other relation payload mappings have unit coverage."];
  assert.equal(hash(path.join(REPO,"miniprogram/project.config.json")),configHash);
  evidence.project_config_sha256=configHash;save();
  console.log(JSON.stringify({run_id:ctx.run_id,status:evidence.status,request_ids:[ctx.first_request_id,ctx.second_request_id],screenshots:evidence.screenshots.length}));
}
main().catch(error=>{
  let message=String(error.stack||error);
  for(const phone of Object.values(ctx.phones))message=message.split(phone).join("[REDACTED_PHONE]");
  message=message.replace(/eyJ[\w-]+\.[\w-]+\.[\w-]+/g,"[REDACTED_TOKEN]");
  evidence.last_error=message;save();console.error(message);process.exitCode=1;
}).finally(async()=>{if(mp){if(logging)await mp.restoreWxMethod("request").catch(()=>{});await mp.disconnect();}});
