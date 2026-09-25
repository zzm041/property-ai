"use strict";

const assert = require("assert/strict");
const crypto = require("crypto");
const fs = require("fs");
const http = require("http");
const path = require("path");
const automator = require("C:/Users/lmj/AppData/Local/Temp/mp-automator-npm/node_modules/miniprogram-automator");

const OUT = path.join(__dirname, "runtime-closeout");
const API_LOG = path.join(
  __dirname, "..", "..", "tmp", "production-v1-closeout", "api-02.stdout.log",
);
const LOWCONF_PRIVATE = path.join(
  __dirname, "..", "..", "tmp", "resident-wo-presentation-020", "context.json",
);
const AUTH_PRIVATE = path.join(
  __dirname, "..", "..", "tmp", "resident-wo-presentation-020", "context.json",
);
const INCIDENT_PRIVATE = path.join(
  __dirname, "..", "..", "tmp", "production-v1-closeout", "incident-disposition", "context.json",
);
const WS = "ws://127.0.0.1:9440";
let miniProgram;
let observing = false;

const sleep = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));
const shortHash = (value) => crypto.createHash("sha256").update(String(value)).digest("hex").slice(0, 16);

async function waitPage(fragment) {
  for (let attempt = 0; attempt < 80; attempt += 1) {
    const page = await miniProgram.currentPage();
    if (page && page.path.includes(fragment)) return page;
    await sleep(250);
  }
  throw new Error(`page timeout: ${fragment}`);
}

async function waitData(page, predicate, label) {
  for (let attempt = 0; attempt < 100; attempt += 1) {
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

async function fill(page, placeholder, value) {
  for (const element of await page.$$("input")) {
    if ((await element.attribute("placeholder")) === placeholder) {
      await element.input(String(value));
      return;
    }
  }
  throw new Error(`input missing: ${placeholder}`);
}

async function tap(element) {
  try {
    await element.tap();
  } catch (error) {
    if (!String(error && error.message).includes("timeout waiting for automator response")) throw error;
  }
}

function testInbox(phone) {
  return new Promise((resolve, reject) => {
    const body = JSON.stringify({ phone, purpose: "login" });
    const request = http.request({
      hostname: "127.0.0.1", port: 8083, path: "/api/v1/auth/sms/test-inbox", method: "POST",
      headers: { "Content-Type": "application/json", "Content-Length": Buffer.byteLength(body) },
    }, (response) => {
      let raw = "";
      response.on("data", (chunk) => { raw += chunk; });
      response.on("end", () => response.statusCode === 200
        ? resolve(JSON.parse(raw).data.code)
        : reject(new Error("Development SMS inbox unavailable")));
    });
    request.on("error", reject);
    request.end(body);
  });
}

async function loginLowConfidenceOwner() {
  const privateContext = JSON.parse(fs.readFileSync(LOWCONF_PRIVATE, "utf8"));
  const phone = privateContext.phones.resident;
  let page = await miniProgram.currentPage();
  if (page && !page.path.includes("pages/login/login")) {
    await miniProgram.callWxMethod("reLaunch", { url: "/pages/home/home" });
    page = await waitPage("pages/home/home");
    const existing = await waitData(page, (state) => Boolean(state.me), "existing resident home");
    if (Number(existing.me.user_id) === Number(privateContext.identities.resident)) return phone;
  }
  let data = page && page.path.includes("pages/login/login") ? await page.data() : {};
  const sentForThisPhone = data.mode === "sms" && data.cooldown > 0 && data.phone === phone;
  if (!sentForThisPhone) {
    await miniProgram.callWxMethod("clearStorageSync");
    await miniProgram.callWxMethod("reLaunch", { url: "/pages/login/login" });
    page = await waitPage("pages/login/login");
    for (const element of await page.$$(".link-text")) {
      if (((await element.text()) || "").includes("验证码登录")) {
        await tap(element);
        break;
      }
    }
    await waitData(page, (state) => state.mode === "sms", "SMS login mode");
    await fill(page, "手机号", phone);
    await tap(await button(page, "获取验证码"));
    data = await waitData(page, (state) => Boolean(state.challengePrompt), "login challenge");
    const match = String(data.challengePrompt).match(/(\d+)\s*\+\s*(\d+)\s*=\s*\?/);
    assert(match);
    await fill(page, "挑战答案", Number(match[1]) + Number(match[2]));
    await tap(await button(page, "获取验证码"));
    for (let attempt = 0; attempt < 40; attempt += 1) {
      data = await page.data();
      if (!data.challengePrompt && data.cooldown > 0) break;
      await sleep(250);
    }
    assert(data.cooldown > 0 && !data.challengePrompt, "Development SMS was not sent");
  }
  await fill(page, "短信验证码", await testInbox(phone));
  await tap(await button(page, "登录"));
  page = await waitPage("pages/home/home");
  data = await waitData(page, (state) => Boolean(state.me), "resident home");
  assert.equal(Number(data.me.user_id), Number(privateContext.identities.resident));
  assert(!data.me.roles.includes("SYSTEM_ADMIN"));
  return phone;
}

async function loginPhone(phone, expectedPermission) {
  await miniProgram.callWxMethod("clearStorageSync");
  await miniProgram.callWxMethod("reLaunch", { url: "/pages/login/login" });
  let page = await waitPage("pages/login/login");
  for (const element of await page.$$(".link-text")) {
    if (((await element.text()) || "").includes("验证码登录")) {
      await tap(element);
      break;
    }
  }
  await waitData(page, (state) => state.mode === "sms", "SMS login mode");
  await fill(page, "手机号", phone);
  await tap(await button(page, "获取验证码"));
  let data = await waitData(page, (state) => Boolean(state.challengePrompt), "login challenge");
  const match = String(data.challengePrompt).match(/(\d+)\s*\+\s*(\d+)\s*=\s*\?/);
  assert(match);
  await fill(page, "挑战答案", Number(match[1]) + Number(match[2]));
  await tap(await button(page, "获取验证码"));
  for (let attempt = 0; attempt < 40; attempt += 1) {
    data = await page.data();
    if (!data.challengePrompt && data.cooldown > 0) break;
    await sleep(250);
  }
  assert(data.cooldown > 0 && !data.challengePrompt, "Development SMS was not sent");
  await fill(page, "短信验证码", await testInbox(phone));
  await tap(await button(page, "登录"));
  page = await waitPage("pages/home/home");
  data = await waitData(page, (state) => Boolean(state.me), "authenticated home");
  assert((data.me.scopes || []).some((scope) =>
    (scope.permissions || []).includes(expectedPermission)
  ));
}

async function observeRequests(requests) {
  await miniProgram.exposeFunction("recordCloseoutRequest", (entry) => requests.push(entry));
  await miniProgram.mockWxMethod("request", function observe(options) {
    const self = this;
    if (!options.url.startsWith("http://127.0.0.1:8083/api/v1/")) {
      throw new Error("request outside approved AUTH001 API");
    }
    return new Promise((resolve, reject) => self.origin({
      ...options,
      success(response) {
        recordCloseoutRequest({
          path: options.url.split("/api/v1")[1],
          method: options.method || "GET",
          status: response.statusCode,
          request_id_hash: shortHash(response.data && response.data.request_id),
          source: "actual origin wx.request",
        });
        if (options.success) options.success(response);
        resolve(response);
      },
      fail(error) {
        if (options.fail) options.fail(error);
        reject(error);
      },
    }));
  });
  observing = true;
}

async function lowConfidence() {
  const privatePhone = await loginLowConfidenceOwner();
  const logOffset = fs.statSync(API_LOG).size;
  await miniProgram.callWxMethod("reLaunch", { url: "/pages/work-orders/detail?id=12" });
  const page = await waitPage("pages/work-orders/detail");
  const data = await waitData(
    page,
    (state) => state.workOrder && Number(state.workOrder.id) === 12,
    "protected low-confidence order",
  );
  assert.equal(data.workOrder.status, "PENDING_ANALYSIS");
  assert.equal(data.needsClarification, true);
  assert.match(data.analysisMessage, /信息不足/);
  const requests = fs.readFileSync(API_LOG).subarray(logOffset).toString("utf8")
    .split(/\r?\n/).filter(Boolean).map((line) => {
      try { return JSON.parse(line); } catch (error) { return null; }
    }).filter((entry) => entry && entry.event === "request_completed"
      && entry.path === "/api/v1/work-orders/12")
    .map((entry) => ({
      path: "/work-orders/12", method: entry.method, status: Number(entry.status_code),
      request_id_hash: shortHash(entry.request_id), source: "API completion for actual page wx.request",
    }));
  assert(requests.some((entry) => entry.status === 200));
  await miniProgram.screenshot({ path: path.join(OUT, "01-low-confidence-clarification.png") });
  fs.writeFileSync(path.join(OUT, "low-confidence-page.json"), JSON.stringify({
    source_commit: "fe90913a6f3918bca33b2f100137a786a67c4f86",
    interaction: "WeChat DevTools actual page navigation and origin wx.request",
    object: { work_order_id: 12, status: data.workOrder.status },
    page: {
      path: page.path,
      needs_clarification: data.needsClarification,
      message_present: Boolean(data.analysisMessage),
    },
    requests,
    business_write: false,
    protected_order_replayed_or_modified: false,
    private_phone_persisted_in_public_evidence: false,
  }, null, 2));
  return privatePhone;
}

async function incidentDisposition() {
  const auth = JSON.parse(fs.readFileSync(AUTH_PRIVATE, "utf8"));
  const state = JSON.parse(fs.readFileSync(INCIDENT_PRIVATE, "utf8"));
  await loginPhone(auth.phones.manager, "incident:manage");
  const logOffset = fs.statSync(API_LOG).size;
  await miniProgram.callWxMethod("reLaunch", {
    url: `/pages/supervisor/incidents?propertyId=${auth.property_id}&incidentId=${state.incident_id}`,
  });
  const page = await waitPage("pages/supervisor/incidents");
  let data = await waitData(
    page,
    (value) => value.selected && Number(value.selected.id) === Number(state.incident_id),
    "Incident detail",
  );
  assert.equal(data.selected.status, "OPEN");
  assert.equal(data.canManage, true);
  const reason = "已核实公共区域风险并进入持续监控";
  for (const element of await page.$$("textarea")) {
    if ((await element.attribute("placeholder")) === "处置或重开原因（必填）") {
      await element.input(reason);
      break;
    }
  }
  await tap(await button(page, "转为监控中"));
  data = await waitData(page, (value) => value.selected && value.selected.status === "MONITORING",
                        "Incident monitoring disposition");
  assert.match(data.notice, /已更新/);
  await miniProgram.screenshot({ path: path.join(OUT, "02-incident-monitoring.png") });
  const requests = fs.readFileSync(API_LOG).subarray(logOffset).toString("utf8")
    .split(/\r?\n/).filter(Boolean).map((line) => {
      try { return JSON.parse(line); } catch (error) { return null; }
    }).filter((entry) => entry && entry.event === "request_completed"
      && String(entry.path || "").includes("/api/v1/supervisor/incidents"))
    .map((entry) => ({
      path: String(entry.path).split("/api/v1")[1], method: entry.method,
      status: Number(entry.status_code), request_id_hash: shortHash(entry.request_id),
      source: "API completion for actual page wx.request",
    }));
  assert(requests.some((entry) => entry.method === "POST" && entry.status === 200));
  fs.writeFileSync(path.join(OUT, "incident-page.json"), JSON.stringify({
    source_commit: "fe90913a6f3918bca33b2f100137a786a67c4f86",
    interaction: "WeChat DevTools actual page navigation, textarea input, button tap, and origin wx.request",
    object: { incident_id: Number(state.incident_id), from_status: "OPEN", to_status: data.selected.status },
    page: { path: page.path, can_manage: data.canManage, notice_present: Boolean(data.notice) },
    requests,
    private_phone_or_token_persisted_in_public_evidence: false,
  }, null, 2));
}

async function main() {
  fs.mkdirSync(OUT, { recursive: true });
  miniProgram = await automator.connect({ wsEndpoint: WS });
  if (process.argv[2] === "low-confidence") await lowConfidence();
  else if (process.argv[2] === "incident") await incidentDisposition();
  else throw new Error("unknown closeout page mode");
}

main().catch((error) => {
  let message = String(error && error.stack ? error.stack : error);
  if (fs.existsSync(LOWCONF_PRIVATE)) {
    const privateContext = JSON.parse(fs.readFileSync(LOWCONF_PRIVATE, "utf8"));
    for (const phone of Object.values(privateContext.phones || {})) {
      message = message.split(String(phone)).join("[REDACTED_PHONE]");
    }
  }
  if (fs.existsSync(AUTH_PRIVATE)) {
    const privateContext = JSON.parse(fs.readFileSync(AUTH_PRIVATE, "utf8"));
    for (const phone of Object.values(privateContext.phones || {})) {
      message = message.split(String(phone)).join("[REDACTED_PHONE]");
    }
  }
  console.error(message.replace(/eyJ[\w-]+\.[\w-]+\.[\w-]+/g, "[REDACTED_TOKEN]"));
  process.exitCode = 1;
}).finally(async () => {
  if (miniProgram) {
    if (observing) await miniProgram.restoreWxMethod("request").catch(() => {});
    await miniProgram.disconnect();
  }
});
