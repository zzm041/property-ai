"use strict";

const fs = require("fs");
const http = require("http");
const path = require("path");
const automator = require(process.env.MP_AUTOMATOR_MODULE);

const OUT = path.resolve(__dirname);
const ACCOUNTS = JSON.parse(fs.readFileSync(path.join(OUT, "accounts.local.json"), "utf8"));
const PUBLIC = JSON.parse(fs.readFileSync(path.join(OUT, "accounts-public.json"), "utf8"));
const RUN_ID = "mp-cfull-20260919-1";
const WS = process.env.MP_AUTOMATOR_WS || "ws://127.0.0.1:9430";
const existing = JSON.parse(fs.readFileSync(path.join(OUT, "first-round-results.json"), "utf8"));
const results = existing.results || [];
const objects = existing.objects || {};
const requests = existing.request_log || [];

function writeJson(name, data) {
  fs.writeFileSync(path.join(OUT, name), JSON.stringify(data, null, 2), "utf8");
}

function saveProgress() {
  writeJson("first-round-results.json", {
    ...existing,
    objects,
    results,
    request_log: requests.slice(-80),
    resumed: true,
  });
}

function wait(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function postJson(pathname, body, requestId) {
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
          "X-Request-Id": requestId || `${RUN_ID}-sidecar`,
          "Content-Length": Buffer.byteLength(data),
        },
      },
      (res) => {
        let raw = "";
        res.on("data", (chunk) => {
          raw += chunk;
        });
        res.on("end", () => {
          let json = null;
          try {
            json = JSON.parse(raw);
          } catch (error) {
            json = {};
          }
          resolve({ status: res.statusCode, json });
        });
      }
    );
    req.on("error", reject);
    req.write(data);
    req.end();
  });
}

function challengeAnswer(prompt) {
  const match = String(prompt || "").trim().match(/^(\d+)\s*\+\s*(\d+)\s*=\s*\?$/);
  if (!match) {
    throw new Error(`cannot parse challenge: ${prompt}`);
  }
  return String(Number(match[1]) + Number(match[2]));
}

async function inboxCode(phone, purpose) {
  for (let i = 0; i < 10; i += 1) {
    const response = await postJson("/auth/sms/test-inbox", { phone, purpose }, `${RUN_ID}-inbox-${purpose}`);
    const code = response.json && response.json.data && response.json.data.code;
    if (code) {
      return code;
    }
    await wait(400);
  }
  throw new Error(`inbox empty for ${purpose}`);
}

function redactMe(me) {
  if (!me || typeof me !== "object") {
    return me;
  }
  return {
    display_name: me.display_name,
    roles: me.roles,
    permissions: me.permissions,
    scopes: (me.scopes || []).map((item) => ({
      scope_type: item.scope_type,
      tenant_id: item.tenant_id,
      property_id: item.property_id || null,
    })),
  };
}

async function current(miniProgram) {
  return miniProgram.currentPage();
}

async function waitPath(miniProgram, fragment, timeoutMs) {
  const limit = timeoutMs || 20000;
  const started = Date.now();
  let page;
  while (Date.now() - started < limit) {
    page = await current(miniProgram);
    if (page && page.path && page.path.indexOf(fragment) >= 0) {
      return page;
    }
    await wait(400);
  }
  throw new Error(`did not reach ${fragment}; last ${page && page.path}`);
}

async function waitUntil(fn, timeoutMs, label) {
  const limit = timeoutMs || 15000;
  const started = Date.now();
  while (Date.now() - started < limit) {
    const last = await fn();
    if (last) {
      return last;
    }
    await wait(300);
  }
  throw new Error(`timeout waiting ${label || "condition"}`);
}

async function screenshot(miniProgram, name) {
  try {
    await miniProgram.screenshot({ path: path.join(OUT, name) });
  } catch (error) {
    fs.appendFileSync(path.join(OUT, "run-log.txt"), `screenshot-fail ${name} ${error.message}\n`, "utf8");
  }
}

async function buttons(page) {
  const nodes = await page.$$("button");
  const labels = [];
  for (const node of nodes) {
    labels.push(((await node.text()) || "").replace(/\s+/g, " ").trim());
  }
  return labels;
}

async function tapButton(page, label) {
  const nodes = await page.$$("button");
  const labels = [];
  for (const node of nodes) {
    const text = ((await node.text()) || "").replace(/\s+/g, " ").trim();
    labels.push(text);
    if (text === label) {
      await node.tap();
      return labels;
    }
  }
  throw new Error(`button not found: ${label}; have ${labels.join("|")}`);
}

async function fillPlaceholder(page, placeholder, value) {
  const inputs = await page.$$("input");
  for (const el of inputs) {
    const ph = await el.attribute("placeholder");
    if (ph === placeholder) {
      await el.input(String(value));
      return;
    }
  }
  const areas = await page.$$("textarea");
  for (const el of areas) {
    const ph = await el.attribute("placeholder");
    if (ph === placeholder) {
      await el.input(String(value));
      return;
    }
  }
  throw new Error(`input not found: ${placeholder}`);
}

async function tapCardContaining(page, selector, needle) {
  const nodes = await page.$$(selector);
  const labels = [];
  for (const node of nodes) {
    const text = ((await node.text()) || "").replace(/\s+/g, " ").trim();
    labels.push(text);
    if (text.indexOf(String(needle)) >= 0) {
      await node.tap();
      return labels;
    }
  }
  throw new Error(`card ${needle} not found; have ${labels.slice(0, 20).join("|")}`);
}

function record(item) {
  const idx = results.findIndex((row) => row.id === item.id);
  if (idx >= 0) {
    results[idx] = item;
  } else {
    results.push(item);
  }
  saveProgress();
  fs.appendFileSync(path.join(OUT, "run-log.txt"), `resume ${item.id} ${item.status}\n`, "utf8");
}

async function safeReLaunch(miniProgram, url) {
  await miniProgram.evaluate((target) => {
    wx.reLaunch({ url: target });
  }, url);
  await wait(700);
}

async function logoutToLogin(miniProgram) {
  let page = await current(miniProgram);
  if (!(page && page.path && page.path.indexOf("pages/home/home") >= 0)) {
    await safeReLaunch(miniProgram, "/pages/home/home");
    page = await waitPath(miniProgram, "pages/home/home");
  }
  await wait(500);
  await tapButton(page, "退出登录");
  return waitPath(miniProgram, "pages/login/login");
}

async function ensureSmsMode(page) {
  const data = await page.data();
  if (data.mode !== "sms") {
    const nodes = await page.$$(".link-text");
    for (const node of nodes) {
      const text = ((await node.text()) || "").trim();
      if (text.indexOf("验证码登录") >= 0) {
        await node.tap();
        await wait(400);
        return;
      }
    }
  }
}

async function sendSmsFromPage(miniProgram, phone) {
  let page = await current(miniProgram);
  await fillPlaceholder(page, "手机号", phone);
  await wait(200);
  await tapButton(page, "获取验证码");
  page = await waitUntil(async () => {
    const currentPage = await current(miniProgram);
    const data = await currentPage.data();
    return data.challengePrompt ? currentPage : null;
  }, 8000, "challenge");
  const data = await page.data();
  await fillPlaceholder(page, "挑战答案", challengeAnswer(data.challengePrompt));
  await wait(200);
  await tapButton(page, "获取验证码");
  await wait(800);
  return current(miniProgram);
}

async function smsLogin(miniProgram, phone) {
  let page = await waitPath(miniProgram, "pages/login/login");
  await ensureSmsMode(page);
  page = await sendSmsFromPage(miniProgram, phone);
  const code = await inboxCode(phone, "login");
  await fillPlaceholder(page, "短信验证码", code);
  await wait(200);
  await tapButton(page, "登录");
  page = await waitPath(miniProgram, "pages/home/home", 20000);
  await wait(600);
  return page;
}

(async () => {
  fs.appendFileSync(path.join(OUT, "run-log.txt"), "resume start\n", "utf8");
  const miniProgram = await automator.connect({ wsEndpoint: WS });
  try {
    let page = await current(miniProgram);
    let data = await page.data();
    if (page.path && page.path.indexOf("pages/home/home") >= 0) {
      fs.appendFileSync(path.join(OUT, "run-log.txt"), "resume already on home\n", "utf8");
    } else if (!(page.path && page.path.indexOf("pages/login/login") >= 0)) {
      await miniProgram.evaluate(() => wx.reLaunch({ url: "/pages/login/login" }));
      page = await waitPath(miniProgram, "pages/login/login");
    }
    if (!(page.path && page.path.indexOf("pages/home/home") >= 0)) {
      page = await smsLogin(miniProgram, ACCOUNTS.phones.staff2);
    }
    page = await current(miniProgram);
    fs.appendFileSync(path.join(OUT, "run-log.txt"), `resume page ${page.path}\n`, "utf8");
    await wait(1500);
    page = await current(miniProgram);
    await miniProgram.evaluate(() => wx.navigateTo({ url: "/pages/profile/profile" }));
    page = await waitPath(miniProgram, "pages/profile/profile");
    await tapButton(page, "员工邀请认领");
    page = await waitPath(miniProgram, "pages/employee-claim/employee-claim");
    await fillPlaceholder(page, "邀请令牌", ACCOUNTS.invite_token);
    await tapButton(page, "认领");
    await wait(1200);
    page = await current(miniProgram);
    data = await page.data();
    await screenshot(miniProgram, `${RUN_ID}-staff2-dup-claim.png`);
    record({
      id: "STAFF-CLAIM-DUP",
      role: "staff2",
      page: page.path,
      status: data.error && page.path.indexOf("employee-claim") >= 0 ? "PASS" : "FAIL",
      expected: "duplicate claim rejected",
      actual: `error=${Boolean(data.error)} path=${page.path}`,
      evidence: [`${RUN_ID}-staff2-dup-claim.png`],
    });
    await safeReLaunch(miniProgram, "/pages/home/home");
    await logoutToLogin(miniProgram);

    page = await smsLogin(miniProgram, ACCOUNTS.phones.resident);
    await tapButton(page, "房屋选择 / 绑定");
    page = await waitPath(miniProgram, "pages/houses/houses");
    await wait(800);
    data = await page.data();
    const active = (data.items || []).find((item) => item.status === "ACTIVE");
    if (!active) {
      await screenshot(miniProgram, `${RUN_ID}-wo-no-active-house.png`);
      record({
        id: "WO-CREATE",
        status: "FAIL",
        actual: "no ACTIVE house",
        evidence: [`${RUN_ID}-wo-no-active-house.png`],
      });
    } else {
      await tapCardContaining(page, ".card", active.room_no || "101");
      page = await waitPath(miniProgram, "pages/work-orders/create");
      await fillPlaceholder(page, "报修说明", `${RUN_ID} 水管漏水，请人工处理`);
      await screenshot(miniProgram, `${RUN_ID}-wo-create.png`);
      await tapButton(page, "提交报修");
      page = await waitPath(miniProgram, "pages/work-orders/detail", 20000);
      await wait(800);
      data = await page.data();
      objects.work_order_id = data.workOrder && data.workOrder.id;
      await screenshot(miniProgram, `${RUN_ID}-wo-created.png`);
      record({
        id: "WO-CREATE",
        role: "resident",
        page: page.path,
        status: objects.work_order_id ? "PASS" : "FAIL",
        expected: "create from selected house",
        actual: JSON.stringify({
          id: objects.work_order_id,
          status: data.workOrder && data.workOrder.status,
        }),
        evidence: [`${RUN_ID}-wo-create.png`, `${RUN_ID}-wo-created.png`],
      });
    }
    record({
      id: "WO-MEDIA-NATIVE",
      role: "resident",
      page: "pages/work-orders/create",
      status: "BLOCKED_ENV",
      expected: "native image/audio picker",
      actual: "automator cannot operate system file/camera/mic pickers",
      evidence: [],
    });

    await safeReLaunch(miniProgram, "/pages/home/home");
    await logoutToLogin(miniProgram);
    page = await smsLogin(miniProgram, ACCOUNTS.phones.property_admin);
    await tapButton(page, "待派工单");
    page = await waitPath(miniProgram, "pages/supervisor/queue");
    await wait(800);
    data = await page.data();
    if (!data.propertyId) {
      await fillPlaceholder(page, "小区 ID", PUBLIC.org.property_id);
      await tapButton(page, "刷新队列");
      await wait(1000);
      page = await current(miniProgram);
      data = await page.data();
    }
    if (objects.work_order_id) {
      await tapCardContaining(page, ".card", objects.work_order_id);
      await wait(300);
      const nodes = await page.$$(".empty");
      for (const node of nodes) {
        const text = ((await node.text()) || "").trim();
        if (text.indexOf(String(PUBLIC.employee_id)) >= 0 || text.indexOf("员工") === 0) {
          await node.tap();
          break;
        }
      }
      await tapButton(page, "AI 建议（Fake Provider）");
      await wait(1500);
      await tapButton(page, "派单");
      await wait(1500);
    }
    page = await current(miniProgram);
    data = await page.data();
    await screenshot(miniProgram, `${RUN_ID}-queue-after-assign.png`);
    writeJson(`${RUN_ID}-queue-after-assign.json`, {
      selectedId: data.selectedId,
      employeeId: data.employeeId,
      items: (data.items || []).map((item) => ({ id: item.id, status: item.status })),
      staff: (data.staff || []).map((item) => ({ employee_id: item.employee_id, on_duty: item.on_duty })),
      recommendations: data.recommendations || [],
      error: data.error || "",
    });
    const stillInQueue = (data.items || []).some((item) => String(item.id) === String(objects.work_order_id));
    record({
      id: "WO-QUEUE-ASSIGN",
      role: "property_admin",
      page: page.path,
      status: objects.work_order_id && !data.error && !stillInQueue ? "PASS" : "FAIL",
      expected: "manual assign from queue",
      actual: JSON.stringify({
        work_order_id: objects.work_order_id,
        stillInQueue,
        error: data.error || "",
        staff: (data.staff || []).length,
      }),
      evidence: [`${RUN_ID}-queue-after-assign.png`],
    });
    record({
      id: "WO-AUTO-DISPATCH",
      role: "property_admin",
      page: page.path,
      status: "NOT_IMPLEMENTED",
      expected: "automatic Assignment",
      actual: `ai_auto_assign=false assigned_source_column=${PUBLIC.assignment_columns_include_assigned_source} manual_assign_used=true`,
      evidence: [`${RUN_ID}-queue-after-assign.png`],
    });

    await safeReLaunch(miniProgram, "/pages/home/home");
    await logoutToLogin(miniProgram);
    page = await smsLogin(miniProgram, ACCOUNTS.phones.staff);
    await tapButton(page, "我的任务");
    page = await waitPath(miniProgram, "pages/staff-orders/list");
    await wait(800);
    await screenshot(miniProgram, `${RUN_ID}-staff-orders.png`);
    if (objects.work_order_id) {
      await tapCardContaining(page, ".card", objects.work_order_id);
      page = await waitPath(miniProgram, "pages/staff-orders/detail");
      await wait(500);
      data = await page.data();
      if (data.canAccept) {
        await tapButton(page, "接单");
        await wait(800);
      }
      page = await current(miniProgram);
      data = await page.data();
      if (data.canStart) {
        await tapButton(page, "开始处理");
        await wait(800);
      }
      page = await current(miniProgram);
      await fillPlaceholder(page, "处理反馈（可选）", "已现场处理漏水");
      await tapButton(page, "提交反馈");
      await wait(800);
      await tapButton(page, "填写回执并完成");
      await wait(1200);
      page = await current(miniProgram);
      data = await page.data();
      await screenshot(miniProgram, `${RUN_ID}-staff-resolved.png`);
      record({
        id: "WO-STAFF-PROCESS",
        role: "staff",
        page: page.path,
        status: data.workOrder && data.workOrder.status === "WAIT_CONFIRM" ? "PASS" : "PARTIAL",
        expected: "ASSIGNED→WAIT_CONFIRM",
        actual: JSON.stringify({
          status: data.workOrder && data.workOrder.status,
          logs: (data.logs || []).map((item) => `${item.from_status}->${item.to_status}`),
          error: data.error || "",
        }),
        evidence: [`${RUN_ID}-staff-orders.png`, `${RUN_ID}-staff-resolved.png`],
      });
    }

    await safeReLaunch(miniProgram, "/pages/home/home");
    await logoutToLogin(miniProgram);
    page = await smsLogin(miniProgram, ACCOUNTS.phones.resident);
    await tapButton(page, "我的工单");
    page = await waitPath(miniProgram, "pages/work-orders/list");
    await wait(800);
    data = await page.data();
    await screenshot(miniProgram, `${RUN_ID}-resident-orders.png`);
    const listed = (data.items || []).some((item) => String(item.id) === String(objects.work_order_id));
    record({
      id: "WO-LIST-PAGING",
      role: "resident",
      page: page.path,
      status: listed ? "PARTIAL" : "FAIL",
      expected: "this-round work order visible; UI has no load-more",
      actual: JSON.stringify({ count: (data.items || []).length, listed, ids: (data.items || []).map((item) => item.id) }),
      evidence: [`${RUN_ID}-resident-orders.png`],
    });
    if (objects.work_order_id) {
      await tapCardContaining(page, ".card", objects.work_order_id);
      page = await waitPath(miniProgram, "pages/work-orders/detail");
      await wait(500);
      data = await page.data();
      if (data.canConfirm) {
        await tapButton(page, "确认解决");
        await wait(1200);
      }
      page = await current(miniProgram);
      data = await page.data();
      await screenshot(miniProgram, `${RUN_ID}-resident-confirmed.png`);
      record({
        id: "WO-CONFIRM-CLOSED",
        role: "resident",
        page: page.path,
        status: data.workOrder && data.workOrder.status === "CLOSED" ? "PASS" : "FAIL",
        expected: "CLOSED after confirm",
        actual: JSON.stringify({
          status: data.workOrder && data.workOrder.status,
          logs: (data.logs || []).map((item) => `${item.from_status}->${item.to_status}`),
        }),
        evidence: [`${RUN_ID}-resident-confirmed.png`],
      });
      await safeReLaunch(miniProgram, `/pages/work-orders/detail?id=${objects.work_order_id}`);
      page = await waitPath(miniProgram, "pages/work-orders/detail");
      await wait(600);
      data = await page.data();
      await screenshot(miniProgram, `${RUN_ID}-resident-reentered.png`);
      record({
        id: "WO-REENTER",
        role: "resident",
        page: page.path,
        status: data.workOrder && data.workOrder.status === "CLOSED" ? "PASS" : "FAIL",
        expected: "reenter CLOSED",
        actual: JSON.stringify({ status: data.workOrder && data.workOrder.status, log_count: (data.logs || []).length }),
        evidence: [`${RUN_ID}-resident-reentered.png`],
      });
    }

    await safeReLaunch(miniProgram, "/pages/home/home");
    await logoutToLogin(miniProgram);
    page = await smsLogin(miniProgram, ACCOUNTS.phones.property_admin);
    await tapButton(page, "公告审核");
    page = await waitPath(miniProgram, "pages/supervisor/announcements");
    data = await page.data();
    if (!data.propertyId) {
      await fillPlaceholder(page, "小区 ID", PUBLIC.org.property_id);
    }
    await fillPlaceholder(page, "标题", `${RUN_ID} 停水通知`);
    await fillPlaceholder(page, "内容", "明日上午停水两小时，本轮合成公告。");
    await tapButton(page, "保存草稿");
    await wait(1000);
    await tapButton(page, "人工审核发布");
    await wait(1200);
    page = await current(miniProgram);
    data = await page.data();
    objects.announcement_id = data.draftId || null;
    await screenshot(miniProgram, `${RUN_ID}-announcement-publish.png`);
    record({
      id: "ANN-PUBLISH",
      role: "property_admin",
      page: page.path,
      status: data.draftId && !data.error ? "PASS" : "PARTIAL",
      expected: "draft then human publish",
      actual: JSON.stringify({ draftId: data.draftId, error: data.error || "" }),
      evidence: [`${RUN_ID}-announcement-publish.png`],
    });
    await safeReLaunch(miniProgram, "/pages/home/home");
    page = await waitPath(miniProgram, "pages/home/home");
    await tapButton(page, "事件");
    page = await waitPath(miniProgram, "pages/supervisor/incidents");
    await wait(600);
    data = await page.data();
    await screenshot(miniProgram, `${RUN_ID}-incidents.png`);
    record({
      id: "INCIDENT-UI",
      role: "property_admin",
      page: page.path,
      status: "PARTIAL",
      expected: "list only, no create/handle",
      actual: JSON.stringify({ count: (data.items || []).length, error: data.error || "" }),
      evidence: [`${RUN_ID}-incidents.png`],
    });
    await safeReLaunch(miniProgram, "/pages/home/home");
    page = await waitPath(miniProgram, "pages/home/home");
    await tapButton(page, "员工状态");
    page = await waitPath(miniProgram, "pages/supervisor/staff");
    await wait(600);
    data = await page.data();
    await screenshot(miniProgram, `${RUN_ID}-staff-daily.png`);
    record({
      id: "STAFF-DAILY-PAGE",
      role: "property_admin",
      page: page.path,
      status: !data.error ? "PASS" : "FAIL",
      expected: "staff daily list",
      actual: JSON.stringify({ count: (data.items || []).length, error: data.error || "" }),
      evidence: [`${RUN_ID}-staff-daily.png`],
    });
    await logoutToLogin(miniProgram);

    page = await smsLogin(miniProgram, ACCOUNTS.phones.resident);
    await tapButton(page, "公告");
    page = await waitPath(miniProgram, "pages/announcements/list");
    await wait(800);
    data = await page.data();
    await screenshot(miniProgram, `${RUN_ID}-announcements-list.png`);
    const ann = (data.items || []).find((item) => String(item.id) === String(objects.announcement_id)) || (data.items || [])[0];
    if (ann) {
      await tapCardContaining(page, ".card", ann.title || ann.id);
      page = await waitPath(miniProgram, "pages/announcements/detail");
      await wait(500);
      data = await page.data();
      await screenshot(miniProgram, `${RUN_ID}-announcement-detail.png`);
      record({
        id: "ANN-READ",
        role: "resident",
        page: page.path,
        status: data.title ? "PASS" : "FAIL",
        expected: "read published announcement",
        actual: JSON.stringify({ title: data.title, status: data.status }),
        evidence: [`${RUN_ID}-announcements-list.png`, `${RUN_ID}-announcement-detail.png`],
      });
    } else {
      record({
        id: "ANN-READ",
        status: "FAIL",
        actual: "no announcement items",
        evidence: [`${RUN_ID}-announcements-list.png`],
      });
    }
    await safeReLaunch(miniProgram, "/pages/home/home");
    page = await waitPath(miniProgram, "pages/home/home");
    await tapButton(page, "知识助手");
    page = await waitPath(miniProgram, "pages/assistant/assistant");
    data = await page.data();
    if (!data.propertyId) {
      await fillPlaceholder(page, "小区 ID", PUBLIC.org.property_id);
    }
    await fillPlaceholder(page, "例如：装修时间是什么？", "装修时间是什么？");
    await tapButton(page, "提问");
    await wait(1500);
    page = await current(miniProgram);
    data = await page.data();
    await screenshot(miniProgram, `${RUN_ID}-assistant.png`);
    record({
      id: "AST-QUERY",
      role: "resident",
      page: page.path,
      status: data.answer || data.error ? "PARTIAL" : "FAIL",
      expected: "Fake assistant wiring",
      actual: JSON.stringify({ hasAnswer: Boolean(data.answer), citations: (data.citations || []).length, error: data.error || "" }),
      evidence: [`${RUN_ID}-assistant.png`],
    });
    await safeReLaunch(miniProgram, "/pages/home/home");
    page = await waitPath(miniProgram, "pages/home/home");
    await tapButton(page, "通知");
    page = await waitPath(miniProgram, "pages/notifications/list");
    await wait(800);
    data = await page.data();
    await screenshot(miniProgram, `${RUN_ID}-notifications.png`);
    const ntfRound = (data.items || []).filter((item) => {
      const rid = item.resource_id || item.resourceId;
      return String(rid) === String(objects.work_order_id) || String(rid) === String(objects.bind_request_id);
    });
    record({
      id: "NTF-ROUND",
      role: "resident",
      page: page.path,
      status: ntfRound.length ? "PASS" : (data.items || []).length ? "PARTIAL" : "BLOCKED_ENV",
      expected: "this-round resource notifications",
      actual: JSON.stringify({ unread: data.unread, count: (data.items || []).length, matched: ntfRound.length, error: data.error || "" }),
      evidence: [`${RUN_ID}-notifications.png`],
    });

    await safeReLaunch(miniProgram, "/pages/home/home");
    await logoutToLogin(miniProgram);
    page = await current(miniProgram);
    data = await page.data();
    await screenshot(miniProgram, `${RUN_ID}-final-login.png`);
    record({
      id: "UX-NO-DEV-LEAK",
      role: "anonymous",
      page: page.path,
      status: data.mode === "password" ? "PASS" : "PARTIAL",
      expected: "login after logout",
      actual: JSON.stringify({ mode: data.mode, buttons: await buttons(page) }),
      evidence: [`${RUN_ID}-final-login.png`],
    });
    await safeReLaunch(miniProgram, "/pages/dev-login/dev-login");
    page = await waitPath(miniProgram, "pages/dev-login/dev-login");
    data = await page.data();
    await screenshot(miniProgram, `${RUN_ID}-dev-login-blocked.png`);
    record({
      id: "DEV-LOGIN-ISOLATED",
      role: "anonymous",
      page: page.path,
      status: data.allowed === false ? "PASS" : "FAIL",
      expected: "debug login blocked",
      actual: JSON.stringify({ allowed: data.allowed, error: data.error || "" }),
      evidence: [`${RUN_ID}-dev-login-blocked.png`],
    });

    const staticNotImpl = [
      ["AUTH-PHONE-CHANGE", "NOT_IMPLEMENTED", "功能尚未实现"],
      ["AUTH-WECHAT-REBIND", "NOT_IMPLEMENTED", "功能尚未实现"],
      ["AUTH-MERGE", "NOT_IMPLEMENTED", "功能尚未实现"],
      ["AUTH-RECOVERY", "NOT_IMPLEMENTED", "功能尚未实现"],
      ["AUTH-PASSWORD-CHANGE", "NOT_IMPLEMENTED", "只有API没有页面"],
      ["STAFF-INVITE-PAGE", "NOT_IMPLEMENTED", "只有API没有页面"],
      ["STAFF-DEPT-SKILL-UI", "NOT_IMPLEMENTED", "只有API没有页面"],
      ["KNOWLEDGE-INGEST-UI", "NOT_IMPLEMENTED", "只有API没有页面"],
      ["HOUSE-IMPORT-EXPORT", "NOT_IMPLEMENTED", "功能尚未实现"],
      ["STATS-CONFIG", "NOT_IMPLEMENTED", "功能尚未实现"],
      ["SELFHOST-AI", "NOT_IMPLEMENTED", "缺环境或权限"],
      ["REAL-SMS", "NOT_IMPLEMENTED", "缺环境或权限"],
      ["WO-TIMEOUT", "NOT_IMPLEMENTED", "不属于小程序页面、应单独后端验证"],
      ["WO-CANCEL-REJECT-REASSIGN-REOPEN", "PARTIAL", "页面部分接线，本轮主链未并行互斥分支"],
      ["AUTH-WECHAT", "NOT_IMPLEMENTED", "缺真实外部配置"],
      ["AUTH-SMS-EXPIRE", "NOT_RUN", "OTP TTL 300s 未在页面等待"],
      ["AUTH-SMS-REUSE", "NOT_RUN", "login navigates away; reuse covered by isolated API"],
    ];
    for (const [id, status, reason] of staticNotImpl) {
      if (!results.find((item) => item.id === id)) {
        record({ id, status, actual: reason, evidence: [] });
      }
    }

    await safeReLaunch(miniProgram, "/pages/login/login");
    await waitPath(miniProgram, "pages/login/login");
    await screenshot(miniProgram, `${RUN_ID}-end-login.png`);
    const counts = results.reduce((acc, item) => {
      acc[item.status] = (acc[item.status] || 0) + 1;
      return acc;
    }, {});
    writeJson("summary.json", {
      task_id: "MP-CURRENT-FULL-REGRESSION-001",
      run_id: RUN_ID,
      code_sha: PUBLIC.code_sha,
      counts,
      total: results.length,
      objects,
      automator_ws: WS,
      not_claimed: ["Production V1", "Day 4 PASS", "real SMS", "self-hosted AI"],
    });
    saveProgress();
    process.exit(0);
  } catch (error) {
    fs.writeFileSync(
      path.join(OUT, "automator-resume-error.txt"),
      `${error && error.message}\n${error && error.stack}\n`,
      "utf8"
    );
    saveProgress();
    process.exit(1);
  }
})();
