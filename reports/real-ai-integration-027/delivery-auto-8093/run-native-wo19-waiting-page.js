"use strict";

const assert = require("assert/strict");
const crypto = require("crypto");
const fs = require("fs");
const path = require("path");

const ROOT = "D:/property-ai";
const OUT = __dirname;
const PRIVATE = path.join(ROOT, "tmp/delivery-auto-8093/fixture-context.json");
const AUTOMATOR = "C:/Users/lmj/AppData/Local/Temp/mp-automator-npm/node_modules/miniprogram-automator";
const WS = "ws://127.0.0.1:9440";
const WORK_ORDER_ID = 19;
const RESULT = path.join(OUT, "native-wo19-waiting-page-final3.json");
const SCREENSHOT = "27-native-wo19-waiting-page.png";
const context = JSON.parse(fs.readFileSync(PRIVATE, "utf8"));

if (fs.existsSync(RESULT)) throw new Error("native waiting-page evidence already exists; refusing duplicate page run");

const evidence = {
  task_id: "REAL-AI-INTEGRATION-027-NATIVE-MEDIA",
  work_order_id: WORK_ORDER_ID,
  interaction: "WeChat DevTools normal resident page login and read-only work-order detail using the original wx.request implementation",
  requests: [],
  screenshots: [],
  boundaries: {
    direct_database_business_write: false,
    manual_analysis_or_assignment: false,
    page_business_mutation: false,
    model_call_from_script: false,
    credentials_or_tokens_saved: false,
  },
};

let miniProgram;
let observing = false;
const sleep = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

function save() {
  fs.writeFileSync(RESULT, `${JSON.stringify(evidence, null, 2)}\n`);
}

async function waitPage(fragment) {
  for (let attempt = 0; attempt < 120; attempt += 1) {
    const page = await miniProgram.currentPage();
    if (page && page.path.includes(fragment)) return page;
    await sleep(250);
  }
  throw new Error(`page timeout: ${fragment}`);
}

async function waitData(page, predicate, label) {
  for (let attempt = 0; attempt < 160; attempt += 1) {
    const data = await page.data();
    if (!data.loading && predicate(data)) return data;
    if (!data.loading && data.error) throw new Error(`${label}: ${data.error}`);
    await sleep(250);
  }
  throw new Error(`state timeout: ${label}`);
}

async function button(page, label) {
  for (const element of await page.$$("button")) {
    if (((await element.text()) || "").trim() === label) return element;
  }
  throw new Error(`button missing: ${label}`);
}

async function field(page, placeholder) {
  for (const element of await page.$$("input")) {
    if ((await element.attribute("placeholder")) === placeholder) return element;
  }
  throw new Error(`field missing: ${placeholder}`);
}

async function tap(element) {
  try {
    await element.tap();
  } catch (error) {
    if (!String(error && error.message).includes("timeout waiting for automator response")) throw error;
  }
}

async function login() {
  await miniProgram.callWxMethod("clearStorageSync");
  await miniProgram.callWxMethod("reLaunch", { url: "/pages/login/login" });
  const page = await waitPage("pages/login/login");
  await waitData(page, (data) => data.mode === "password", "resident password mode");
  await (await field(page, "手机号")).input(context.phones.residentA1);
  await (await field(page, "密码")).input(context.passwords.residentA1);
  await tap(await button(page, "登录"));
  const home = await waitPage("pages/home/home");
  const data = await waitData(home, (value) => Boolean(value.me && value.me.user_id), "resident home");
  assert.equal(Number(data.me.user_id), 6);
}

async function main() {
  miniProgram = await require(AUTOMATOR).connect({ wsEndpoint: WS });
  await login();
  await miniProgram.callWxMethod("navigateTo", { url: `/pages/work-orders/detail?id=${WORK_ORDER_ID}` });
  const page = await waitPage("pages/work-orders/detail");
  const data = await waitData(page, (value) => (
    value.workOrder &&
    Number(value.workOrder.id) === WORK_ORDER_ID &&
    value.workOrder.status === "PENDING_ASSIGNMENT" &&
    value.media.length === 3 &&
    value.logs.some((item) => item.event_code === "AUTO_DISPATCH_WAIT")
  ), "native work order waiting detail");
  assert.equal(data.autoDispatched, false);
  assert.equal(data.item.assignments.length, 0);
  assert.equal(Boolean(data.item.asr_text), true);
  evidence.page_result = {
    status: data.workOrder.status,
    status_label: data.statusLabel,
    media: data.media.map((item) => ({ id: item.id, file_id: item.file_id, kind: item.kind })),
    asr_text_present: true,
    asr_text_sha256: crypto.createHash("sha256").update(data.item.asr_text).digest("hex"),
    assignment_count: data.item.assignments.length,
    auto_dispatched: data.autoDispatched,
    wait_event_present: data.logs.some((item) => item.event_code === "AUTO_DISPATCH_WAIT"),
    can_append: data.canAppend,
    can_cancel: data.canCancel,
  };
  await miniProgram.screenshot({ path: path.join(OUT, SCREENSHOT) });
  evidence.screenshots.push(SCREENSHOT);
  save();
  await miniProgram.callWxMethod("reLaunch", { url: "/pages/home/home" });
  console.log(JSON.stringify(evidence.page_result));
}

main().catch((error) => {
  let message = String(error && (error.stack || error.message) || error);
  for (const value of [...Object.values(context.phones), ...Object.values(context.passwords)]) {
    message = message.split(String(value)).join("[REDACTED]");
  }
  evidence.error = message.replace(/eyJ[\w-]+\.[\w-]+\.[\w-]+/g, "[REDACTED_TOKEN]");
  save();
  console.error(evidence.error);
  process.exitCode = 1;
}).finally(async () => {
  if (miniProgram) {
    if (observing) await miniProgram.restoreWxMethod("request").catch(() => {});
    await miniProgram.disconnect();
  }
});
