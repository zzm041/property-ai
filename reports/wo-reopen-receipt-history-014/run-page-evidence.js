"use strict";
// Actual controls/navigation and real wx.request. No injected business state.
const fs = require("fs");
const path = require("path");
const http = require("http");
const crypto = require("crypto");
const assert = require("assert/strict");
const automator = require(process.env.MP_AUTOMATOR_MODULE);
const REPO = path.resolve(__dirname, "../..");
const PRIVATE = path.join(REPO, "tmp/wo-reopen-receipt-history-014/context.json");
const ctx = JSON.parse(fs.readFileSync(PRIVATE, "utf8"));
const EVIDENCE = path.join(__dirname, "page-evidence.json");
const evidence = fs.existsSync(EVIDENCE) ? JSON.parse(fs.readFileSync(EVIDENCE, "utf8")) : {
  task_id: "WO-REOPEN-RECEIPT-HISTORY-014", run_id: ctx.run_id,
  code_sha: process.env.MP_CODE_SHA, steps: {}, requests: [], screenshots: [],
  mode: "WeChat DevTools real controls, Development SMS/AI, real AUTH001 API/Worker/DB; not physical-device or Production acceptance",
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
    if (!allowError && !data.loading && data.error) throw new Error(`${label}: ${data.error}`);
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
async function openOrder(home, staff) {
  const listing = await navigate(home, staff ? "/pages/staff-orders/list" : "/pages/work-orders/list");
  await ready(listing, state => state.items && state.items.some(row => Number(row.id) === ctx.page_order_id), "order list");
  await tap(await attr(listing, ".card", "data-id", ctx.page_order_id));
  const page = await current(staff ? "pages/staff-orders/detail" : "pages/work-orders/detail");
  await ready(page, state => state.workOrder && Number(state.workOrder.id) === ctx.page_order_id, "same order");
  return page;
}
async function snapshot(page, name) {
  const data = await page.data();
  const output = {
    work_order: data.workOrder, processes: data.processHistory || [],
    receipts: (data.item && data.item.receipts) || [], logs: data.logs || [],
    error: data.error || "", can_reopen: data.canReopen,
  };
  evidence.steps[name] = output;
  await sleep(500);
  const filename = `${name}.png`;
  await mp.screenshot({path:path.join(__dirname, filename)});
  if(!evidence.screenshots.includes(filename)) evidence.screenshots.push(filename);
  save();
  return data;
}
async function attachLog() {
  await mp.exposeFunction("recordWo014Request", data => {
    if(!data.url.startsWith("http://127.0.0.1:8083/api/v1/")) throw new Error("Unexpected API target");
    const relative = data.url.replace("http://127.0.0.1:8083/api/v1", "");
    if (!/^\/(work-orders|staff\/work-orders|auth\/sms\/login|me)/.test(relative)) return;
    evidence.requests.push({method:data.method || "GET", path:relative, status:data.status,
      received_at_utc:new Date().toISOString(),source:"real wx.request forwarded through supported automator logging proxy"});
    save();
  });
  await mp.mockWxMethod("request", function(options) {
    const self=this;
    return new Promise((resolve,reject) => self.origin({...options,
      success(response) { recordWo014Request({url:options.url,method:options.method,status:response.statusCode}); if(options.success) options.success(response); resolve(response); },
      fail(error) { if(options.fail) options.fail(error); reject(error); },
    }));
  });
  logging=true;
}
async function finishReceipt(page, cycle) {
  await fill(page, "例如：电气维修", "电气维修");
  await fill(page, "说明检查、处理步骤和结果", `${ctx.run_id} 第${cycle}轮：检查灯具接线并完成复测`);
  await fill(page, "无材料可留空", `第${cycle}轮绝缘接线材料`);
  await fill(page, "无需跟进可留空", `第${cycle}轮处理结果请住户复核`);
  await tap(await byText(page, "提交完整回执并完成"));
  await ready(page, state => state.workOrder.status === "WAIT_CONFIRM", `receipt cycle ${cycle}`);
}
async function main() {
  assert(process.env.MP_CODE_SHA && process.env.MP_AUTOMATOR_WS);
  assert.equal(evidence.code_sha, process.env.MP_CODE_SHA);
  mp = await automator.connect({wsEndpoint:process.env.MP_AUTOMATOR_WS});
  await attachLog();
  if(!ctx.page_order_id) {
    const home = await login("resident");
    let page = await navigate(home, "/pages/work-orders/create");
    await fill(page, "房屋 ID", ctx.house_id);
    await fill(page, "报修说明", `${ctx.run_id} 页面重开验证：客厅灯不亮，开关接触不良。`);
    await tap(await byText(page, "提交报修"));
    page = await current("pages/work-orders/detail");
    const state = await ready(page, value => value.workOrder && value.workOrder.id, "created order");
    ctx.page_order_id=Number(state.workOrder.id); save();
    await ready(page, value => value.workOrder.status === "ASSIGNED", "Worker AUTO assignment");
    const assigned = await snapshot(page, "01-auto-assigned");
    const assignment=assigned.item.assignments.at(-1);
    assert.equal(assignment.reason,"AUTO_DISPATCH");
    ctx.page_staff = Object.keys(ctx.employees).find(key => ctx.employees[key] === assignment.employee_id);
    assert(ctx.page_staff); save();
  }
  if(!evidence.steps["02-first-receipt"]) {
    const home = await login(ctx.page_staff);
    const page = await openOrder(home, true);
    let state=await page.data();
    if(state.workOrder.status === "ASSIGNED") { await tap(await byText(page,"接单")); await ready(page,s=>s.workOrder.status==="ACCEPTED","accepted"); }
    state=await page.data();
    if(state.workOrder.status === "ACCEPTED") { await tap(await byText(page,"开始处理")); await ready(page,s=>s.workOrder.status==="PROCESSING","started"); }
    state=await page.data();
    if(state.workOrder.status === "PROCESSING") await finishReceipt(page,1);
    await snapshot(page,"02-first-receipt");
  }
  if(!evidence.steps["04-reopened-second-cycle"]) {
    const home=await login("resident"); const page=await openOrder(home,false);
    let state=await page.data();
    if(state.workOrder.status === "WAIT_CONFIRM") {
      await tap(await byText(page,"未解决重开"));
      await ready(page,s=>s.error.includes("1至255"),"blank reason rejected",true);
      await snapshot(page,"03-empty-reason-rejected");
      await fill(page,"请说明仍未解决的问题（必填）",`${ctx.run_id} 住户反馈：灯仍然闪烁，请继续检查。`);
      await tap(await byText(page,"未解决重开"));
      await ready(page,s=>s.workOrder.status==="PROCESSING","reopened",true);
    }
    state=await snapshot(page,"04-reopened-second-cycle");
    assert.equal(state.processHistory.length,2);
    assert.equal(state.item.receipts.length,1);
    assert.equal(state.processHistory[0].feedbacks[0].unresolved_flag,true);
    assert.equal(state.workOrder.resolved_at,null);
  }
  if(!evidence.steps["05-second-receipt"]) {
    const home=await login(ctx.page_staff); const page=await openOrder(home,true);
    const state=await page.data();
    if(state.workOrder.status === "PROCESSING") await finishReceipt(page,2);
    await snapshot(page,"05-second-receipt");
  }
  if(!evidence.steps["07-closed-refresh"]) {
    const home=await login("resident"); const page=await openOrder(home,false);
    await snapshot(page,"06-two-cycle-history");
    const state=await page.data();
    assert.equal(state.item.receipts.length,2);
    if(state.workOrder.status === "WAIT_CONFIRM") {
      await tap(await byText(page,"确认解决"));
      await ready(page,s=>s.workOrder.status==="CLOSED","confirmed closed");
    }
    await tap(await byText(page,"刷新"));
    await ready(page,s=>s.workOrder.status==="CLOSED" && s.item.receipts.length===2,"refresh retained history");
    await snapshot(page,"07-closed-refresh");
  }
  if(!evidence.steps["08-closed-relogin"]) {
    const home=await login("resident"); const page=await openOrder(home,false);
    const state=await snapshot(page,"08-closed-relogin");
    assert.equal(state.workOrder.status,"CLOSED");
    assert.equal(state.item.receipts.length,2);
    assert.equal(state.processHistory[0].receipt.method, `${ctx.run_id} 第1轮：检查灯具接线并完成复测`);
    assert.equal(state.processHistory[1].receipt.method, `${ctx.run_id} 第2轮：检查灯具接线并完成复测`);
  }
  assert.equal(hash(path.join(REPO,"miniprogram/project.config.json")),configHash);
  evidence.status="PAGE_FLOW_PASS_PENDING_INDEPENDENT_REVIEW";
  evidence.project_config_sha256=configHash; save();
  console.log(JSON.stringify({run_id:ctx.run_id,work_order_id:ctx.page_order_id,status:evidence.status,screenshots:evidence.screenshots.length}));
}
main().catch(error => {
  let message=String(error.stack || error);
  for(const phone of Object.values(ctx.phones)) message=message.split(phone).join("[REDACTED_PHONE]");
  message=message.replace(/eyJ[\w-]+\.[\w-]+\.[\w-]+/g,"[REDACTED_TOKEN]");
  evidence.last_error=message; save(); console.error(message); process.exitCode=1;
}).finally(async () => {
  if(mp) { if(logging) await mp.restoreWxMethod("request").catch(()=>{}); await mp.disconnect(); }
});
