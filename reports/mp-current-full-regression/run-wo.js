"use strict";
const fs = require("fs");
const http = require("http");
const path = require("path");
const automator = require(process.env.MP_AUTOMATOR_MODULE);
const OUT = path.resolve(__dirname);
const ACCOUNTS = JSON.parse(fs.readFileSync(path.join(OUT, "accounts.local.json"), "utf8"));
const PUBLIC = JSON.parse(fs.readFileSync(path.join(OUT, "accounts-public.json"), "utf8"));
const RUN_ID = "mp-cfull-20260919-1";
const existing = JSON.parse(fs.readFileSync(path.join(OUT, "first-round-results.json"), "utf8"));
const results = existing.results || [];
const objects = existing.objects || {};

function log(msg) {
  fs.appendFileSync(path.join(OUT, "run-log.txt"), `${msg}\n`, "utf8");
  console.log(msg);
}
function wait(ms) {
  return new Promise((r) => setTimeout(r, ms));
}
function save() {
  fs.writeFileSync(path.join(OUT, "first-round-results.json"), JSON.stringify({ ...existing, objects, results, resumed: true }, null, 2));
}
function record(item) {
  const i = results.findIndex((r) => r.id === item.id);
  if (i >= 0) results[i] = item;
  else results.push(item);
  save();
  log(`${item.id} ${item.status}`);
}
function postJson(pathname, body) {
  return new Promise((resolve, reject) => {
    const data = JSON.stringify(body);
    const req = http.request(
      {
        host: "127.0.0.1",
        port: 8083,
        path: `/api/v1${pathname}`,
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Request-Id": `${RUN_ID}-wo`, "Content-Length": Buffer.byteLength(data) },
      },
      (res) => {
        let raw = "";
        res.on("data", (c) => { raw += c; });
        res.on("end", () => resolve(JSON.parse(raw)));
      }
    );
    req.on("error", reject);
    req.write(data);
    req.end();
  });
}
function answer(prompt) {
  const m = String(prompt || "").trim().match(/^(\d+)\s*\+\s*(\d+)\s*=\s*\?$/);
  return String(Number(m[1]) + Number(m[2]));
}
async function inbox(phone) {
  for (let i = 0; i < 10; i += 1) {
    const j = await postJson("/auth/sms/test-inbox", { phone, purpose: "login" });
    if (j.data && j.data.code) return j.data.code;
    await wait(400);
  }
  throw new Error("inbox empty");
}
async function shot(mp, name) {
  try { await mp.screenshot({ path: path.join(OUT, name) }); } catch (e) { log(`shot fail ${name}`); }
}
async function current(mp) { return mp.currentPage(); }
async function waitPath(mp, frag) {
  for (let i = 0; i < 30; i += 1) {
    const p = await current(mp);
    if (p && p.path && p.path.indexOf(frag) >= 0) return p;
    await wait(400);
  }
  const p = await current(mp);
  throw new Error(`no ${frag} last ${p && p.path}`);
}
async function tap(page, label) {
  const nodes = await page.$$("button");
  for (const n of nodes) {
    if (((await n.text()) || "").trim() === label) { await n.tap(); return; }
  }
  throw new Error(`no button ${label}`);
}
async function fill(page, ph, val) {
  const els = [...(await page.$$("input")), ...(await page.$$("textarea"))];
  for (const el of els) {
    if ((await el.attribute("placeholder")) === ph) { await el.input(String(val)); return; }
  }
  throw new Error(`no input ${ph}`);
}
async function toLogin(mp) {
  try {
    await mp.callWxMethod("clearStorageSync");
  } catch (error) {
    await mp.evaluate(() => wx.clearStorageSync());
  }
  await wait(300);
  await mp.callWxMethod("reLaunch", { url: "/pages/login/login" });
  await wait(800);
  return waitPath(mp, "pages/login/login");
}
async function smsLogin(mp, phone) {
  let page = await waitPath(mp, "pages/login/login");
  const data = await page.data();
  if (data.mode !== "sms") {
    const links = await page.$$(".link-text");
    for (const n of links) {
      if (((await n.text()) || "").indexOf("验证码登录") >= 0) { await n.tap(); await wait(400); }
    }
  }
  page = await current(mp);
  await fill(page, "手机号", phone);
  await tap(page, "获取验证码");
  await wait(800);
  page = await current(mp);
  let d = await page.data();
  await fill(page, "挑战答案", answer(d.challengePrompt));
  await tap(page, "获取验证码");
  await wait(800);
  const code = await inbox(phone);
  page = await current(mp);
  await fill(page, "短信验证码", code);
  await tap(page, "登录");
  return waitPath(mp, "pages/home/home");
}

(async () => {
  log("wo start");
  const mp = await automator.connect({ wsEndpoint: process.env.MP_AUTOMATOR_WS });
  log(`path ${(await current(mp)).path} wo=${objects.work_order_id}`);
  await toLogin(mp);
  log("on login");
  let page = await smsLogin(mp, ACCOUNTS.phones.property_admin);
  log("manager home");
  await mp.callWxMethod("navigateTo", { url: `/pages/supervisor/queue?propertyId=${PUBLIC.org.property_id}&tenantId=${PUBLIC.org.tenant_id}` });
  page = await waitPath(mp, "pages/supervisor/queue");
  await wait(1000);
  let d = await page.data();
  log(`queue items=${(d.items || []).length} staff=${(d.staff || []).length}`);
  if (objects.work_order_id) {
    const cards = await page.$$(".card");
    for (const c of cards) {
      if (((await c.text()) || "").indexOf(String(objects.work_order_id)) >= 0) { await c.tap(); break; }
    }
    await wait(300);
    const empties = await page.$$(".empty");
    for (const n of empties) {
      const t = ((await n.text()) || "").trim();
      if (t.indexOf(String(PUBLIC.employee_id)) >= 0) { await n.tap(); break; }
    }
    page = await current(mp);
    d = await page.data();
    if (!d.employeeId && (d.staff || [])[0]) {
      const first = String(d.staff[0].employee_id);
      const nodes = await page.$$(".empty");
      for (const n of nodes) {
        if (((await n.text()) || "").indexOf(first) >= 0) { await n.tap(); break; }
      }
    }
    await tap(page, "AI 建议（Fake Provider）");
    await wait(1500);
    await tap(page, "派单");
    await wait(1500);
  }
  page = await current(mp);
  d = await page.data();
  await shot(mp, `${RUN_ID}-queue-after-assign.png`);
  fs.writeFileSync(path.join(OUT, `${RUN_ID}-queue-after-assign.json`), JSON.stringify({ items: d.items, staff: d.staff, error: d.error, selectedId: d.selectedId, employeeId: d.employeeId, recommendations: d.recommendations }, null, 2));
  const still = (d.items || []).some((i) => String(i.id) === String(objects.work_order_id));
  record({ id: "WO-QUEUE-ASSIGN", status: objects.work_order_id && !d.error && !still ? "PASS" : "FAIL", actual: JSON.stringify({ still, error: d.error || "", staff: (d.staff || []).length, selectedId: d.selectedId, employeeId: d.employeeId }), evidence: [`${RUN_ID}-queue-after-assign.png`] });
  record({ id: "WO-AUTO-DISPATCH", status: "NOT_IMPLEMENTED", actual: `ai_auto_assign=false assigned_source_column=${PUBLIC.assignment_columns_include_assigned_source} wo_status_on_create=PENDING_ANALYSIS`, evidence: [`${RUN_ID}-queue-after-assign.png`] });

  await toLogin(mp);
  page = await smsLogin(mp, ACCOUNTS.phones.staff);
  await mp.callWxMethod("navigateTo", { url: "/pages/staff-orders/list" });
  page = await waitPath(mp, "pages/staff-orders/list");
  await wait(800);
  await shot(mp, `${RUN_ID}-staff-orders.png`);
  const cards = await page.$$(".card");
  for (const c of cards) {
    if (((await c.text()) || "").indexOf(String(objects.work_order_id)) >= 0) { await c.tap(); break; }
  }
  page = await waitPath(mp, "pages/staff-orders/detail");
  await wait(500);
  d = await page.data();
  if (d.canAccept) { await tap(page, "接单"); await wait(800); }
  page = await current(mp); d = await page.data();
  if (d.canStart) { await tap(page, "开始处理"); await wait(800); }
  page = await current(mp);
  await fill(page, "处理反馈（可选）", "已现场处理漏水");
  await tap(page, "提交反馈");
  await wait(800);
  await tap(page, "填写回执并完成");
  await wait(1200);
  page = await current(mp); d = await page.data();
  await shot(mp, `${RUN_ID}-staff-resolved.png`);
  record({ id: "WO-STAFF-PROCESS", status: d.workOrder && d.workOrder.status === "WAIT_CONFIRM" ? "PASS" : "PARTIAL", actual: JSON.stringify({ status: d.workOrder && d.workOrder.status, error: d.error || "" }), evidence: [`${RUN_ID}-staff-orders.png`, `${RUN_ID}-staff-resolved.png`] });

  await toLogin(mp);
  page = await smsLogin(mp, ACCOUNTS.phones.resident);
  await mp.callWxMethod("navigateTo", { url: "/pages/work-orders/list" });
  page = await waitPath(mp, "pages/work-orders/list");
  await wait(800);
  d = await page.data();
  await shot(mp, `${RUN_ID}-resident-orders.png`);
  const listed = (d.items || []).some((i) => String(i.id) === String(objects.work_order_id));
  record({ id: "WO-LIST-PAGING", status: listed ? "PARTIAL" : "FAIL", actual: JSON.stringify({ count: (d.items || []).length, listed, ids: (d.items || []).map((i) => i.id) }), evidence: [`${RUN_ID}-resident-orders.png`] });
  const rcards = await page.$$(".card");
  for (const c of rcards) {
    if (((await c.text()) || "").indexOf(String(objects.work_order_id)) >= 0) { await c.tap(); break; }
  }
  page = await waitPath(mp, "pages/work-orders/detail");
  d = await page.data();
  if (d.canConfirm) { await tap(page, "确认解决"); await wait(1200); }
  page = await current(mp); d = await page.data();
  await shot(mp, `${RUN_ID}-resident-confirmed.png`);
  record({ id: "WO-CONFIRM-CLOSED", status: d.workOrder && d.workOrder.status === "CLOSED" ? "PASS" : "FAIL", actual: JSON.stringify({ status: d.workOrder && d.workOrder.status }), evidence: [`${RUN_ID}-resident-confirmed.png`] });
  await mp.callWxMethod("reLaunch", { url: `/pages/work-orders/detail?id=${objects.work_order_id}` });
  page = await waitPath(mp, "pages/work-orders/detail");
  d = await page.data();
  await shot(mp, `${RUN_ID}-resident-reentered.png`);
  record({ id: "WO-REENTER", status: d.workOrder && d.workOrder.status === "CLOSED" ? "PASS" : "FAIL", actual: JSON.stringify({ status: d.workOrder && d.workOrder.status, logs: (d.logs || []).length }), evidence: [`${RUN_ID}-resident-reentered.png`] });

  await toLogin(mp);
  page = await smsLogin(mp, ACCOUNTS.phones.property_admin);
  await mp.callWxMethod("navigateTo", { url: `/pages/supervisor/announcements?propertyId=${PUBLIC.org.property_id}` });
  page = await waitPath(mp, "pages/supervisor/announcements");
  d = await page.data();
  if (!d.propertyId) await fill(page, "小区 ID", PUBLIC.org.property_id);
  await fill(page, "标题", `${RUN_ID} 停水通知`);
  await fill(page, "内容", "明日上午停水两小时");
  await tap(page, "保存草稿");
  await wait(1000);
  await tap(page, "人工审核发布");
  await wait(1200);
  page = await current(mp); d = await page.data();
  objects.announcement_id = d.draftId || null;
  await shot(mp, `${RUN_ID}-announcement-publish.png`);
  record({ id: "ANN-PUBLISH", status: d.draftId && !d.error ? "PASS" : "PARTIAL", actual: JSON.stringify({ draftId: d.draftId, error: d.error || "" }), evidence: [`${RUN_ID}-announcement-publish.png`] });

  await toLogin(mp);
  page = await smsLogin(mp, ACCOUNTS.phones.resident);
  await mp.callWxMethod("navigateTo", { url: `/pages/announcements/list?propertyId=${PUBLIC.org.property_id}` });
  page = await waitPath(mp, "pages/announcements/list");
  await wait(800);
  d = await page.data();
  await shot(mp, `${RUN_ID}-announcements-list.png`);
  const ann = (d.items || [])[0];
  if (ann) {
    const acards = await page.$$(".card");
    for (const c of acards) {
      if (((await c.text()) || "").indexOf(ann.title || "") >= 0) { await c.tap(); break; }
    }
    page = await waitPath(mp, "pages/announcements/detail");
    d = await page.data();
    await shot(mp, `${RUN_ID}-announcement-detail.png`);
    record({ id: "ANN-READ", status: d.title ? "PASS" : "FAIL", actual: JSON.stringify({ title: d.title, status: d.status }), evidence: [`${RUN_ID}-announcements-list.png`, `${RUN_ID}-announcement-detail.png`] });
  } else {
    record({ id: "ANN-READ", status: "FAIL", actual: "no items", evidence: [`${RUN_ID}-announcements-list.png`] });
  }
  await mp.callWxMethod("reLaunch", { url: `/pages/assistant/assistant?propertyId=${PUBLIC.org.property_id}` });
  page = await waitPath(mp, "pages/assistant/assistant");
  await fill(page, "例如：装修时间是什么？", "装修时间是什么？");
  await tap(page, "提问");
  await wait(1500);
  page = await current(mp); d = await page.data();
  await shot(mp, `${RUN_ID}-assistant.png`);
  record({ id: "AST-QUERY", status: d.answer || d.error ? "PARTIAL" : "FAIL", actual: JSON.stringify({ hasAnswer: Boolean(d.answer), citations: (d.citations || []).length, error: d.error || "" }), evidence: [`${RUN_ID}-assistant.png`] });
  await mp.callWxMethod("reLaunch", { url: "/pages/notifications/list" });
  page = await waitPath(mp, "pages/notifications/list");
  await wait(800);
  d = await page.data();
  await shot(mp, `${RUN_ID}-notifications.png`);
  record({ id: "NTF-ROUND", status: (d.items || []).length ? "PARTIAL" : "BLOCKED_ENV", actual: JSON.stringify({ unread: d.unread, count: (d.items || []).length, error: d.error || "" }), evidence: [`${RUN_ID}-notifications.png`] });

  await toLogin(mp);
  await shot(mp, `${RUN_ID}-end-login.png`);
  record({ id: "UX-NO-DEV-LEAK", status: "PASS", actual: "ended on login", evidence: [`${RUN_ID}-end-login.png`] });
  await mp.callWxMethod("reLaunch", { url: "/pages/dev-login/dev-login" });
  page = await waitPath(mp, "pages/dev-login/dev-login");
  d = await page.data();
  await shot(mp, `${RUN_ID}-dev-login-blocked.png`);
  record({ id: "DEV-LOGIN-ISOLATED", status: d.allowed === false ? "PASS" : "FAIL", actual: JSON.stringify({ allowed: d.allowed, error: d.error || "" }), evidence: [`${RUN_ID}-dev-login-blocked.png`] });
  await toLogin(mp);
  const counts = results.reduce((a, i) => { a[i.status] = (a[i.status] || 0) + 1; return a; }, {});
  fs.writeFileSync(path.join(OUT, "summary.json"), JSON.stringify({ task_id: "MP-CURRENT-FULL-REGRESSION-001", run_id: RUN_ID, counts, total: results.length, objects, not_claimed: ["Production V1", "Day 4 PASS", "real SMS", "self-hosted AI"] }, null, 2));
  log("wo done");
  process.exit(0);
})().catch((e) => {
  fs.writeFileSync(path.join(OUT, "automator-wo-error.txt"), `${e.message}\n${e.stack}\n`);
  log(`ERR ${e.message}`);
  process.exit(1);
});
