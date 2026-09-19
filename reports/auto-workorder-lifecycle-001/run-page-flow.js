"use strict";

const fs = require("fs");
const http = require("http");
const path = require("path");
const automator = require(process.env.MP_AUTOMATOR_MODULE);

const OUT = __dirname;
const ACCOUNTS = JSON.parse(
  fs.readFileSync(
    path.join(OUT, "..", "mp-current-full-regression", "accounts.local.json"),
    "utf8"
  )
);
const WS = process.env.MP_AUTOMATOR_WS || "ws://127.0.0.1:9435";
const WORK_ORDER_ID = 5;

function wait(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function postJson(pathname, body) {
  return new Promise((resolve, reject) => {
    const data = JSON.stringify(body);
    const request = http.request(
      {
        host: "127.0.0.1",
        port: 8083,
        path: `/api/v1${pathname}`,
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Content-Length": Buffer.byteLength(data),
          "X-Request-Id": "auto-workorder-lifecycle-page-flow",
        },
      },
      (response) => {
        let raw = "";
        response.on("data", (chunk) => {
          raw += chunk;
        });
        response.on("end", () => {
          let payload = {};
          try {
            payload = raw ? JSON.parse(raw) : {};
          } catch (error) {
            reject(new Error("Development SMS inbox returned invalid JSON"));
            return;
          }
          if (response.statusCode >= 400) {
            reject(new Error(`Development SMS inbox status ${response.statusCode}`));
            return;
          }
          resolve(payload);
        });
      }
    );
    request.on("error", reject);
    request.write(data);
    request.end();
  });
}

function challengeAnswer(prompt) {
  const match = String(prompt || "").match(/^(\d+)\s*\+\s*(\d+)\s*=\s*\?$/);
  if (!match) {
    throw new Error("Unsupported visual challenge prompt");
  }
  return String(Number(match[1]) + Number(match[2]));
}

async function current(miniProgram) {
  return miniProgram.currentPage();
}

async function waitPath(miniProgram, fragment) {
  const expected = String(fragment).replace(/^\//, "");
  for (let attempt = 0; attempt < 40; attempt += 1) {
    const page = await current(miniProgram);
    if (page && page.path && page.path.includes(expected)) {
      return page;
    }
    await wait(300);
  }
  const page = await current(miniProgram);
  throw new Error(`Expected ${expected}; current=${page && page.path}`);
}

async function tap(page, label) {
  for (const node of await page.$$("button")) {
    if (((await node.text()) || "").trim() === label) {
      await node.tap();
      return;
    }
  }
  throw new Error(`Button not found: ${label}`);
}

async function fill(page, placeholder, value) {
  const fields = [...(await page.$$("input")), ...(await page.$$("textarea"))];
  for (const field of fields) {
    if ((await field.attribute("placeholder")) === placeholder) {
      await field.input(String(value));
      return;
    }
  }
  throw new Error(`Field not found: ${placeholder}`);
}

async function resetToLogin(miniProgram) {
  await miniProgram.callWxMethod("clearStorageSync");
  await miniProgram.callWxMethod("reLaunch", { url: "/pages/login/login" });
  return waitPath(miniProgram, "pages/login/login");
}

async function smsLogin(miniProgram, phone) {
  let page = await waitPath(miniProgram, "pages/login/login");
  if ((await page.data()).mode !== "sms") {
    for (const node of await page.$$(".link-text")) {
      if (((await node.text()) || "").includes("验证码登录")) {
        await node.tap();
        break;
      }
    }
  }
  page = await current(miniProgram);
  await fill(page, "手机号", phone);
  await tap(page, "获取验证码");
  await wait(500);
  page = await current(miniProgram);
  await fill(page, "挑战答案", challengeAnswer((await page.data()).challengePrompt));
  await tap(page, "获取验证码");
  await wait(500);
  const inbox = await postJson("/auth/sms/test-inbox", { phone, purpose: "login" });
  await fill(await current(miniProgram), "短信验证码", inbox.data.code);
  await tap(await current(miniProgram), "登录");
  return waitPath(miniProgram, "pages/home/home");
}

async function openDetail(miniProgram, route) {
  await miniProgram.callWxMethod("reLaunch", { url: `${route}?id=${WORK_ORDER_ID}` });
  const page = await waitPath(miniProgram, route);
  for (let attempt = 0; attempt < 30; attempt += 1) {
    const data = await page.data();
    if (data.workOrder && Number(data.workOrder.id) === WORK_ORDER_ID) {
      return page;
    }
    await wait(300);
  }
  throw new Error(`Work order ${WORK_ORDER_ID} did not load on ${route}`);
}

async function waitStatus(page, expected) {
  for (let attempt = 0; attempt < 40; attempt += 1) {
    const data = await page.data();
    if (data.workOrder && data.workOrder.status === expected) {
      return data;
    }
    if (data.error) {
      throw new Error(`Page action failed: ${data.error}`);
    }
    await wait(300);
  }
  const data = await page.data();
  throw new Error(`Expected status ${expected}; current=${data.workOrder && data.workOrder.status}`);
}

async function screenshot(miniProgram, name) {
  await miniProgram.screenshot({ path: path.join(OUT, name) });
}

(async () => {
  const miniProgram = await automator.connect({ wsEndpoint: WS });
  const transitions = [];
  let page;

  if (process.env.RESUME_CURRENT_STAFF_SESSION === "1") {
    page = await waitPath(miniProgram, "pages/staff-orders/detail");
  } else {
    await resetToLogin(miniProgram);
    await smsLogin(miniProgram, ACCOUNTS.phones.staff);
    page = await openDetail(miniProgram, "/pages/staff-orders/detail");
  }
  let staffData = await page.data();

  if (staffData.workOrder.status === "ASSIGNED") {
    await screenshot(miniProgram, "01-staff-auto-assigned.png");
    await tap(page, "接单");
    staffData = await waitStatus(page, "ACCEPTED");
    transitions.push("ASSIGNED->ACCEPTED");
  }
  if (staffData.workOrder.status === "ACCEPTED") {
    await tap(page, "开始处理");
    staffData = await waitStatus(page, "PROCESSING");
    transitions.push("ACCEPTED->PROCESSING");
  }
  if (staffData.workOrder.status === "PROCESSING") {
    await fill(page, "例如：电气维修", "电气维修");
    await fill(page, "说明检查、处理步骤和结果", "现场检查插座接线，紧固松动端子并完成通电复测，运行正常。");
    await fill(page, "无材料可留空", "绝缘胶带");
    await fill(page, "无需跟进可留空", "住户继续观察，如再次出现火花请立即重开工单。");
    await screenshot(miniProgram, "02-staff-complete-receipt.png");
    await tap(page, "提交完整回执并完成");
    staffData = await waitStatus(page, "WAIT_CONFIRM");
    transitions.push("PROCESSING->WAIT_CONFIRM");
  }
  if (!["WAIT_CONFIRM", "CLOSED"].includes(staffData.workOrder.status)) {
    throw new Error(`Unexpected staff status ${staffData.workOrder.status}`);
  }
  await screenshot(miniProgram, "03-staff-awaiting-confirmation.png");

  await resetToLogin(miniProgram);
  await smsLogin(miniProgram, ACCOUNTS.phones.resident);
  page = await openDetail(miniProgram, "/pages/work-orders/detail");
  let residentData = await page.data();
  const receiptBeforeConfirm = residentData.latestReceipt;
  if (!receiptBeforeConfirm) {
    throw new Error("Resident detail did not expose the submitted receipt");
  }
  if (residentData.workOrder.status === "WAIT_CONFIRM") {
    await screenshot(miniProgram, "04-resident-receipt-wait-confirm.png");
    await tap(page, "确认解决");
    residentData = await waitStatus(page, "CLOSED");
    transitions.push("WAIT_CONFIRM->COMPLETED->CLOSED");
  }
  if (residentData.workOrder.status !== "CLOSED") {
    throw new Error(`Unexpected resident status ${residentData.workOrder.status}`);
  }
  await screenshot(miniProgram, "05-resident-closed.png");

  await resetToLogin(miniProgram);
  await smsLogin(miniProgram, ACCOUNTS.phones.resident);
  page = await openDetail(miniProgram, "/pages/work-orders/detail");
  const reentered = await page.data();
  await screenshot(miniProgram, "06-resident-relogin-closed.png");

  const result = {
    task_id: "AUTO-WO-LIFECYCLE-001",
    captured_at: new Date().toISOString(),
    code_sha: process.env.CODE_SHA || null,
    automator_ws: WS,
    work_order_id: WORK_ORDER_ID,
    page_interactions: [
      "staff accept button",
      "staff start button",
      "staff complete receipt form and submit button",
      "resident receipt review and confirm button",
      "resident fresh SMS login and detail re-entry",
    ],
    transitions_executed_this_run: transitions,
    receipt_before_confirm: {
      problem_type: receiptBeforeConfirm.problem_type,
      method: receiptBeforeConfirm.method,
      materials: receiptBeforeConfirm.materials,
      fee_flag: receiptBeforeConfirm.fee_flag,
      fee_amount: receiptBeforeConfirm.fee_amount,
      resolved: receiptBeforeConfirm.resolved,
      follow_up: receiptBeforeConfirm.follow_up,
      finished_at: receiptBeforeConfirm.finished_at,
    },
    final_status: residentData.workOrder.status,
    relogin_status: reentered.workOrder && reentered.workOrder.status,
    relogin_receipt_matches:
      Boolean(reentered.latestReceipt) &&
      reentered.latestReceipt.id === receiptBeforeConfirm.id,
    final_log_events: (reentered.logs || []).map((item) => item.event_code),
    manual_supervisor_interaction_used: false,
    screenshots: [
      "01-staff-auto-assigned.png",
      "02-staff-complete-receipt.png",
      "03-staff-awaiting-confirmation.png",
      "04-resident-receipt-wait-confirm.png",
      "05-resident-closed.png",
      "06-resident-relogin-closed.png",
    ],
    not_claimed: [
      "persistent production media",
      "real SMS",
      "real AI",
      "Production V1",
      "formal release",
    ],
    redaction: "No phone, SMS code, token, credential, or resident name is stored.",
  };
  fs.writeFileSync(
    path.join(OUT, "page-evidence.json"),
    `${JSON.stringify(result, null, 2)}\n`,
    "utf8"
  );
  miniProgram.disconnect();
  console.log(JSON.stringify(result));
  process.exit(
    result.final_status === "CLOSED" &&
      result.relogin_status === "CLOSED" &&
      result.relogin_receipt_matches
      ? 0
      : 2
  );
})().catch((error) => {
  fs.writeFileSync(
    path.join(OUT, "page-flow-error.txt"),
    `${error.message}\n${error.stack || ""}\n`,
    "utf8"
  );
  console.error(error.message);
  process.exit(1);
});
