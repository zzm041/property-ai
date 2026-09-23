"use strict";
// Actual controls/navigation and real wx.request. No injected business state.
const fs = require("fs");
const path = require("path");
const http = require("http");
const crypto = require("crypto");
const assert = require("assert/strict");
const automator = require(process.env.MP_AUTOMATOR_MODULE);
const REPO = path.resolve(__dirname, "../..");
const PRIVATE = path.join(REPO, "tmp/house-catalog-paging-016/context.json");
const ctx = JSON.parse(fs.readFileSync(PRIVATE, "utf8"));
const EVIDENCE = path.join(__dirname, "fix1-page-evidence.json");
const evidence = fs.existsSync(EVIDENCE) ? JSON.parse(fs.readFileSync(EVIDENCE, "utf8")) : {
  task_id: "HOUSE-CATALOG-PAGING-016", run_id: ctx.run_id,
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


async function snap(page,name,selector) {
  console.log(JSON.stringify({stage:name}));
  await mp.callWxMethod("pageScrollTo",{scrollTop:0,duration:0});
  if(selector){const el=await page.$(selector);assert(el,selector);const offset=await el.offset();await mp.callWxMethod("pageScrollTo",{scrollTop:Math.max(0,offset.top-12),duration:0});}
  const d=await page.data();
  evidence.steps[name]={path:page.path,house_ids:(d.houses||[]).map(r=>r.id),selectedHouseId:d.selectedHouseId,
    active_relations:(d.items||[]).filter(r=>r.relation_id).map(r=>({relation_id:r.relation_id,house_id:r.house_id,relation_type:r.relation_type})),
    pending_requests:(d.items||[]).filter(r=>r.requested_at).map(r=>({id:r.id,house_id:r.house_id,status:r.status,user_id:r.user_id})),
    activeCursor:d.activeCursor,houseCursor:d.houseCursor,nextCursor:d.nextCursor,error:d.error};
  await sleep(450);const file=name+".png";
  try{await mp.screenshot({path:path.join(__dirname,file)});}catch(e){if(!e.message.includes("timeout"))throw e;await mp.screenshot({path:path.join(__dirname,file)});}
  if(!evidence.screenshots.includes(file))evidence.screenshots.push(file);save();
}
async function logs(){
 await mp.exposeFunction("recordPaging016",data=>{
   if(!data.url.startsWith("http://127.0.0.1:8083/api/v1/"))throw Error("wrong environment");
   const relative=data.url.replace("http://127.0.0.1:8083/api/v1","");
   if(!/^\/(properties|buildings|units|me\/houses|admin\/resident-bind-requests)/.test(relative))return;
   evidence.requests.push({path:relative,method:data.method||"GET",status:data.status,
    request_id:data.requestId,result:data.result,received_at_utc:new Date().toISOString(),source:"actual origin-forwarded wx.request, no fake response"});
   save();
 });
 await mp.mockWxMethod("request",function(options){const self=this;return new Promise((resolve,reject)=>self.origin({...options,
  success(response){recordPaging016({url:options.url,method:options.method,status:response.statusCode,requestId:response.data.request_id,result:response.data.data});if(options.success)options.success(response);resolve(response);},
  fail(error){if(options.fail)options.fail(error);reject(error);}
 }));});logging=true;
}
async function main(){
 assert.equal(evidence.code_sha,process.env.MP_CODE_SHA);
 assert(!evidence.status,"Fix1 already completed: do not replay");
 const api=JSON.parse(fs.readFileSync(path.join(__dirname,"fix1-api-database-queue-evidence.json"),"utf8"));
 assert.equal(api.code_sha,evidence.code_sha);assert.equal(api.run_id,ctx.run_id);
 mp=await automator.connect({wsEndpoint:process.env.MP_AUTOMATOR_WS});await logs();
 const home=await login("resident"),page=await navigate(home,"/pages/houses/houses");
 await ready(page,d=>!d.catalogLoading&&d.houses.length===50,"mixed-state first page");
 let data=await page.data();
 assert(!data.houses.some(h=>h.id===ctx.houses[0]));
 assert(!data.houses.some(h=>h.id===ctx.target_house_id));assert(data.houseCursor);
 assert.deepEqual(data.houses.map(h=>h.id),api.api.first.map(h=>h.id));
 await snap(page,"fix1-01-mixed-state-first-page","#house-picker");
 await tap(await byText(page,"加载更多房号"));
 await ready(page,d=>!d.moreLoading&&d.houses.length===52&&!d.houseCursor,"mixed-state next page");
 data=await page.data();
 assert.equal(new Set(data.houses.map(h=>h.id)).size,52);
 assert(!data.houses.some(h=>h.id===ctx.houses[0]));
 assert.deepEqual(data.houses.map(h=>h.id),[...api.api.first,...api.api.second].map(h=>h.id));
 const index=data.houses.findIndex(h=>h.id===ctx.target_house_id);assert(index>=50);
 await(await page.$("#house-picker")).trigger("change",{value:index});
 await ready(page,d=>d.selectedHouseId===ctx.target_house_id,"later active house selected");
 await snap(page,"fix1-02-later-active-house-selected","#house-picker");
 // Await real request logger completion, not only page rendering.
 await sleep(500);
 assert(evidence.requests.some(r=>r.path.includes("/houses?")&&r.path.includes("after_id=51")&&r.status===200));
 evidence.status="FIX1_PAGE_PASS_PENDING_INDEPENDENT_REVIEW";
 evidence.finding="HOUSE016-B1";evidence.repair_round=1;
 evidence.limits=["Only B1 mixed-state catalog regression. Initial016 and015 passed workflows not replayed.",
 "Fixture changed one marked016 house to INACTIVE through normal manager API; page uses real GET/click/select, no fake data.",
 "Picker uses supported change event, not native wheel. No real provider/device/production claim."];
 assert.equal(hash(path.join(REPO,"miniprogram/project.config.json")),configHash);
 evidence.project_config_sha256=configHash;save();
 console.log(JSON.stringify({run_id:ctx.run_id,status:evidence.status,screenshots:evidence.screenshots.length}));
}
main().catch(error=>{let m=String(error.stack||error);for(const p of Object.values(ctx.phones))m=m.split(p).join("[REDACTED_PHONE]");
 evidence.last_error=m.replace(/eyJ[\w-]+\.[\w-]+\.[\w-]+/g,"[REDACTED_TOKEN]");save();console.error(evidence.last_error);process.exitCode=1;
}).finally(async()=>{if(mp){if(logging)await mp.restoreWxMethod("request").catch(()=>{});await mp.disconnect();}});
