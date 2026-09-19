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
  fs.writeFileSync(
    path.join(OUT, "first-round-results.json"),
    JSON.stringify({ ...existing, objects, results, resumed: true }, null, 2),
    "utf8"
  );
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
        headers: {
          "Content-Type": "application/json",
          "X-Request-Id": `${RUN_ID}-sidecar`,
          "Content-Length": Buffer.byteLength(data),
        },
      },
      (res) => {
        let raw = "";
        res.on("data", (c) => {
          raw += c;
        });
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
  try {
    await mp.screenshot({ path: path.join(OUT, name) });
  } catch (e) {
    log(`shot fail ${name}`);
  }
}
async function current(mp) {
  return mp.currentPage();
}
async function waitPath(mp, frag) {
  for (let i = 0; i < 25; i += 1) {
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
    if (((await n.text()) || "").trim() === label) {
      await n.tap();
      return;
    }
  }
  throw new Error(`no button ${label}`);
}
async function fill(page, ph, val) {
  const els = [...(await page.$$("input")), ...(await page.$$("textarea"))];
  for (const el of els) {
    if ((await el.attribute("placeholder")) === ph) {
      await el.input(String(val));
      return;
    }
  }
  throw new Error(`no input ${ph}`);
}
async function go(mp, url) {
  await mp.evaluate((u) => wx.navigateTo({ url: u }), url);
  await wait(800);
}
async function relaunch(mp, url) {
  await mp.evaluate((u) => wx.reLaunch({ url: u }), url);
  await wait(800);
}
async function smsLogin(mp, phone) {
  let page = await waitPath(mp, "pages/login/login");
  const data = await page.data();
  if (data.mode !== "sms") {
    const links = await page.$$(".link-text");
    for (const n of links) {
      if (((await n.text()) || "").indexOf("验证码登录") >= 0) {
        await n.tap();
        await wait(400);
      }
    }
  }
  page = await current(mp);
  await fill(page, "手机号", phone);
  await tap(page, "获取验证码");
  await wait(800);
  page = await current(mp);
  let d = await page.data();
  if (!d.challengePrompt) throw new Error("no challenge");
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
  log("mini start");
  const mp = await automator.connect({ wsEndpoint: process.env.MP_AUTOMATOR_WS });
  log("connected");
  let page = await current(mp);
  log(`startpath ${page.path}`);
  if (!(page.path && page.path.indexOf("login") >= 0)) {
    await relaunch(mp, "/pages/login/login");
    page = await waitPath(mp, "pages/login/login");
  }
  page = await smsLogin(mp, ACCOUNTS.phones.staff2);
  log("staff2 home");
  await go(mp, "/pages/employee-claim/employee-claim");
  page = await waitPath(mp, "pages/employee-claim/employee-claim");
  await fill(page, "邀请令牌", ACCOUNTS.invite_token);
  await tap(page, "认领");
  await wait(1200);
  page = await current(mp);
  let d = await page.data();
  await shot(mp, `${RUN_ID}-staff2-dup-claim.png`);
  record({
    id: "STAFF-CLAIM-DUP",
    status: d.error && page.path.indexOf("employee-claim") >= 0 ? "PASS" : "FAIL",
    page: page.path,
    actual: `error=${Boolean(d.error)} path=${page.path}`,
    evidence: [`${RUN_ID}-staff2-dup-claim.png`],
    note: "navigateTo used after home button tap hung on degraded session",
  });
  await relaunch(mp, "/pages/login/login");
  page = await smsLogin(mp, ACCOUNTS.phones.resident);
  log("resident home");
  await go(mp, "/pages/houses/houses");
  page = await waitPath(mp, "pages/houses/houses");
  await wait(800);
  d = await page.data();
  const active = (d.items || []).find((i) => i.status === "ACTIVE");
  if (!active) {
    record({ id: "WO-CREATE", status: "FAIL", actual: "no ACTIVE house", evidence: [] });
  } else {
    const cards = await page.$$(".card");
    for (const c of cards) {
      const t = ((await c.text()) || "").trim();
      if (t.indexOf(active.room_no || "101") >= 0) {
        await c.tap();
        break;
      }
    }
    page = await waitPath(mp, "pages/work-orders/create");
    await fill(page, "报修说明", `${RUN_ID} 水管漏水`);
    await shot(mp, `${RUN_ID}-wo-create.png`);
    await tap(page, "提交报修");
    page = await waitPath(mp, "pages/work-orders/detail");
    await wait(600);
    d = await page.data();
    objects.work_order_id = d.workOrder && d.workOrder.id;
    await shot(mp, `${RUN_ID}-wo-created.png`);
    record({
      id: "WO-CREATE",
      status: objects.work_order_id ? "PASS" : "FAIL",
      actual: JSON.stringify({ id: objects.work_order_id, status: d.workOrder && d.workOrder.status }),
      evidence: [`${RUN_ID}-wo-create.png`, `${RUN_ID}-wo-created.png`],
    });
  }
  record({
    id: "WO-MEDIA-NATIVE",
    status: "BLOCKED_ENV",
    actual: "automator cannot operate native pickers",
    evidence: [],
  });
  await relaunch(mp, "/pages/login/login");
  page = await smsLogin(mp, ACCOUNTS.phones.property_admin);
  await go(mp, `/pages/supervisor/queue?propertyId=${PUBLIC.org.property_id}&tenantId=${PUBLIC.org.tenant_id}`);
  page = await waitPath(mp, "pages/supervisor/queue");
  await wait(800);
  if (objects.work_order_id) {
    const cards = await page.$$(".card");
    for (const c of cards) {
      const t = ((await c.text()) || "").trim();
      if (t.indexOf(String(objects.work_order_id)) >= 0) {
        await c.tap();
        break;
      }
    }
    const empties = await page.$$(".empty");
    for (const n of empties) {
      const t = ((await n.text()) || "").trim();
      if (t.indexOf(String(PUBLIC.employee_id)) >= 0) {
        await n.tap();
        break;
      }
    }
    await tap(page, "AI 建议（Fake Provider）");
    await wait(1200);
    await tap(page, "派单");
    await wait(1200);
  }
  page = await current(mp);
  d = await page.data();
  await shot(mp, `${RUN_ID}-queue-after-assign.png`);
  const still = (d.items || []).some((i) => String(i.id) === String(objects.work_order_id));
  record({
    id: "WO-QUEUE-ASSIGN",
    status: objects.work_order_id && !d.error && !still ? "PASS" : "FAIL",
    actual: JSON.stringify({ id: objects.work_order_id, still, error: d.error || "", staff: (d.staff || []).length }),
    evidence: [`${RUN_ID}-queue-after-assign.png`],
  });
  record({
    id: "WO-AUTO-DISPATCH",
    status: "NOT_IMPLEMENTED",
    actual: `ai_auto_assign=false assigned_source=${PUBLIC.assignment_columns_include_assigned_source}`,
    evidence: [`${RUN_ID}-queue-after-assign.png`],
  });
  await relaunch(mp, "/pages/login/login");
  page = await smsLogin(mp, ACCOUNTS.phones.staff);
  await go(mp, "/pages/staff-orders/list");
  page = await waitPath(mp, "pages/staff-orders/list");
  await wait(600);
  await shot(mp, `${RUN_ID}-staff-orders.png`);
  if (objects.work_order_id) {
    const cards = await page.$$(".card");
    for (const c of cards) {
      if (((await c.text()) || "").indexOf(String(objects.work_order_id)) >= 0) {
        await c.tap();
        break;
      }
    }
    page = await waitPath(mp, "pages/staff-orders/detail");
    await wait(500);
    d = await page.data();
    if (d.canAccept) {
      await tap(page, "接单");
      await wait(800);
    }
    page = await current(mp);
    d = await page.data();
    if (d.canStart) {
      await tap(page, "开始处理");
      await wait(800);
    }
    page = await current(mp);
    await fill(page, "处理反馈（可选）", "已现场处理");
    await tap(page, "提交反馈");
    await wait(800);
    await tap(page, "填写回执并完成");
    await wait(1200);
    page = await current(mp);
    d = await page.data();
    await shot(mp, `${RUN_ID}-staff-resolved.png`);
    record({
      id: "WO-STAFF-PROCESS",
      status: d.workOrder && d.workOrder.status === "WAIT_CONFIRM" ? "PASS" : "PARTIAL",
      actual: JSON.stringify({ status: d.workOrder && d.workOrder.status, error: d.error || "" }),
      evidence: [`${RUN_ID}-staff-orders.png`, `${RUN_ID}-staff-resolved.png`],
    });
  }
  await relaunch(mp, "/pages/login/login");
  page = await smsLogin(mp, ACCOUNTS.phones.resident);
  await go(mp, "/pages/work-orders/list");
  page = await waitPath(mp, "pages/work-orders/list");
  await wait(600);
  d = await page.data();
  await shot(mp, `${RUN_ID}-resident-orders.png`);
  const listed = (d.items || []).some((i) => String(i.id) === String(objects.work_order_id));
  record({
    id: "WO-LIST-PAGING",
    status: listed ? "PARTIAL" : "FAIL",
    actual: JSON.stringify({ count: (d.items || []).length, listed }),
    evidence: [`${RUN_ID}-resident-orders.png`],
  });
  if (objects.work_order_id) {
    const cards = await page.$$(".card");
    for (const c of cards) {
      if (((await c.text()) || "").indexOf(String(objects.work_order_id)) >= 0) {
        await c.tap();
        break;
      }
    }
    page = await waitPath(mp, "pages/work-orders/detail");
    d = await page.data();
    if (d.canConfirm) {
      await tap(page, "确认解决");
      await wait(1200);
    }
    page = await current(mp);
    d = await page.data();
    await shot(mp, `${RUN_ID}-resident-confirmed.png`);
    record({
      id: "WO-CONFIRM-CLOSED",
      status: d.workOrder && d.workOrder.status === "CLOSED" ? "PASS" : "FAIL",
      actual: JSON.stringify({ status: d.workOrder && d.workOrder.status }),
      evidence: [`${RUN_ID}-resident-confirmed.png`],
    });
  }
  await relaunch(mp, "/pages/login/login");
  await waitPath(mp, "pages/login/login");
  await shot(mp, `${RUN_ID}-end-login.png`);
  const counts = results.reduce((a, i) => {
    a[i.status] = (a[i.status] || 0) + 1;
    return a;
  }, {});
  fs.writeFileSync(path.join(OUT, "summary.json"), JSON.stringify({ counts, total: results.length, objects, automator_ws: process.env.MP_AUTOMATOR_WS }, null, 2));
  log("mini done");
  process.exit(0);
})().catch((e) => {
  fs.writeFileSync(path.join(OUT, "automator-resume-error.txt"), `${e.message}\n${e.stack}\n`);
  log(`ERR ${e.message}`);
  process.exit(1);
});
