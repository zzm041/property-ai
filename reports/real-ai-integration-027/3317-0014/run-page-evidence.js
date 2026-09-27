"use strict";

const crypto = require("crypto");
const fs = require("fs");
const http = require("http");
const path = require("path");
const automator = require(process.env.MP_AUTOMATOR_MODULE);

const ROOT = path.resolve(__dirname, "..", "..", "..");
const CONTEXT = process.env.AUTH001_IDENTITY_CONTEXT;
const API_LOG = path.join(ROOT, "tmp", "auth001-retry-3317", "runtime", "api-v2.stdout.log");
const OUTPUT = path.join(__dirname, "page-evidence.json");
const WS = "ws://127.0.0.1:9440";
const RECAPTURE_TARGET = process.env.MP_RECAPTURE_TARGET === "1";
let privateRun;

function wait(ms) { return new Promise((resolve) => setTimeout(resolve, ms)); }
function shortHash(value) { return crypto.createHash("sha256").update(String(value)).digest("hex").slice(0, 16); }

async function tolerateTimeout(operation) {
  try { return await operation(); }
  catch (error) {
    if (!String(error && error.message).includes("timeout waiting for automator response")) throw error;
    await wait(400);
    return null;
  }
}

async function waitPath(miniProgram, expected) {
  for (let attempt = 0; attempt < 80; attempt += 1) {
    const page = await miniProgram.currentPage();
    if (page && page.path === expected) return page;
    await wait(250);
  }
  const page = await miniProgram.currentPage();
  throw new Error(`expected ${expected}; current=${page && page.path}`);
}

async function waitData(page, predicate, label) {
  for (let attempt = 0; attempt < 100; attempt += 1) {
    const data = await page.data();
    if (predicate(data)) return data;
    await wait(250);
  }
  throw new Error(`${label} timed out`);
}

async function button(page, text) {
  for (const item of await page.$$("button")) {
    const value = ((await item.text()) || "").replace(/\s+/g, " ").trim();
    if (value.includes(text)) return item;
  }
  throw new Error(`button not found: ${text}`);
}

async function buttonUrl(page, url) {
  for (const item of await page.$$("button")) {
    if ((await item.attribute("data-url")) === url) return item;
  }
  throw new Error(`navigation button not found: ${url}`);
}

async function tapText(page, selector, text) {
  for (const item of await page.$$(selector)) {
    const value = ((await item.text()) || "").replace(/\s+/g, " ").trim();
    if (value.includes(text)) {
      await tolerateTimeout(() => item.tap());
      return;
    }
  }
  throw new Error(`text not found: ${text}`);
}

async function pickerByPrefix(page, prefix) {
  for (const item of await page.$$("picker")) {
    const value = ((await item.text()) || "").replace(/\s+/g, " ").trim();
    if (value.startsWith(prefix)) return item;
  }
  throw new Error(`picker not found: ${prefix}`);
}

async function input(page, placeholder, value) {
  for (const item of await page.$$("input")) {
    if ((await item.attribute("placeholder")) === placeholder) {
      await item.input(String(value));
      return;
    }
  }
  throw new Error(`input not found: ${placeholder}`);
}

async function tapTo(miniProgram, element, expectedPath) {
  await tolerateTimeout(() => element.tap());
  return waitPath(miniProgram, expectedPath);
}

async function reset(miniProgram) {
  await miniProgram.callWxMethod("clearStorageSync");
  await miniProgram.callWxMethod("reLaunch", { url: "/pages/login/login" });
  return waitPath(miniProgram, "pages/login/login");
}

async function passwordLogin(miniProgram, role) {
  let page = await reset(miniProgram);
  await input(page, "手机号", privateRun.phones[role]);
  await input(page, "密码", privateRun.passwords[role]);
  page = await tapTo(miniProgram, await button(page, "登录"), "pages/home/home");
  const state = await waitData(page, (data) => data.me && !data.loading, `${role} home`);
  return { page, state };
}

function answer(prompt) {
  const match = String(prompt || "").match(/^(\d+)\s*\+\s*(\d+)\s*=\s*\?$/);
  if (!match) throw new Error("unsupported challenge prompt");
  return String(Number(match[1]) + Number(match[2]));
}

function testInbox(phone) {
  return new Promise((resolve, reject) => {
    const data = JSON.stringify({ phone, purpose: "login" });
    const request = http.request({
      host: "127.0.0.1", port: 8083, method: "POST", path: "/api/v1/auth/sms/test-inbox",
      headers: { "Content-Type": "application/json", "Content-Length": Buffer.byteLength(data) },
    }, (response) => {
      let raw = "";
      response.on("data", (chunk) => { raw += chunk; });
      response.on("end", () => {
        const payload = JSON.parse(raw);
        if (response.statusCode !== 200 || !payload.data || !payload.data.code) reject(new Error("test inbox failed"));
        else resolve(String(payload.data.code));
      });
    });
    request.on("error", reject); request.write(data); request.end();
  });
}

async function smsLogin(miniProgram, role) {
  let page = await reset(miniProgram);
  await tapText(page, ".link-text", "验证码登录");
  await waitData(page, (data) => data.mode === "sms", `${role} sms mode`);
  await input(page, "手机号", privateRun.phones[role]);
  await tolerateTimeout(() => button(page, "获取验证码").then((item) => item.tap()));
  let state = await waitData(page, (data) => data.challengePrompt && !data.loading, `${role} challenge`);
  await input(page, "挑战答案", answer(state.challengePrompt));
  await tolerateTimeout(() => button(page, "获取验证码").then((item) => item.tap()));
  await waitData(page, (data) => !data.challengePrompt && data.cooldown > 0 && !data.loading, `${role} delivery`);
  await input(page, "短信验证码", await testInbox(privateRun.phones[role]));
  page = await tapTo(miniProgram, await button(page, "登录"), "pages/home/home");
  state = await waitData(page, (data) => data.me && !data.loading, `${role} home`);
  return { page, state };
}

function requestEvidence(offset) {
  const lines = fs.readFileSync(API_LOG).subarray(offset).toString("utf8").split(/\r?\n/).filter(Boolean);
  return lines.map((line) => { try { return JSON.parse(line); } catch (_) { return null; } })
    .filter((item) => item && item.event === "request_completed")
    .map((item) => ({ method: item.method, path: item.path, status: item.status_code, request_id_hash: shortHash(item.request_id) }));
}

(async () => {
  if (!CONTEXT || !fs.existsSync(CONTEXT)) throw new Error("private context missing");
  if (fs.existsSync(OUTPUT) && !RECAPTURE_TARGET) throw new Error("page evidence already exists");
  privateRun = JSON.parse(fs.readFileSync(CONTEXT, "utf8"));
  const runtime = JSON.parse(fs.readFileSync(path.join(__dirname, "identity-sla-runtime-evidence.json"), "utf8"));
  const offset = fs.statSync(API_LOG).size;
  const miniProgram = await automator.connect({ wsEndpoint: WS });
  try {
    if (RECAPTURE_TARGET) {
      const target = await passwordLogin(miniProgram, "target");
      let targetPage = await tapTo(miniProgram, await buttonUrl(target.page, "/pages/profile/profile"), "pages/profile/profile");
      targetPage = await tapTo(miniProgram, await button(targetPage, "账号合并 / 高风险恢复"), "pages/identity-recovery/identity-recovery");
      const targetState = await waitData(targetPage, (data) => !data.loading && data.capabilities && data.cases.length, "target identity page recapture");
      const completedCase = targetState.cases.find((item) => Number(item.id) === Number(runtime.identity.case_id));
      if (!completedCase || completedCase.status !== "COMPLETED" || completedCase.approval_count !== 2) throw new Error("completed identity case not shown during recapture");
      await tolerateTimeout(() => miniProgram.callWxMethod("pageScrollTo", { scrollTop: 99999, duration: 0 }));
      await wait(1500);
      await miniProgram.screenshot({ path: path.join(__dirname, "01-target-completed-identity.png") });
      const evidence = JSON.parse(fs.readFileSync(OUTPUT, "utf8"));
      evidence.checks.target_completed_identity_visual_recaptured = true;
      evidence.requests = evidence.requests.concat(requestEvidence(offset));
      fs.writeFileSync(OUTPUT, JSON.stringify(evidence, null, 2));
      console.log(JSON.stringify({ case_id: completedCase.id, status: completedCase.status, recaptured: true }));
      return;
    }
    const targetScreenshot = path.join(__dirname, "01-target-completed-identity.png");
    let completed;
    let page;
    if (fs.existsSync(targetScreenshot)) {
      completed = { id: runtime.identity.case_id, status: runtime.identity.final_status, approval_count: 2 };
    } else {
      const target = await passwordLogin(miniProgram, "target");
      page = await tapTo(miniProgram, await buttonUrl(target.page, "/pages/profile/profile"), "pages/profile/profile");
      page = await tapTo(miniProgram, await button(page, "账号合并 / 高风险恢复"), "pages/identity-recovery/identity-recovery");
      const targetState = await waitData(page, (data) => !data.loading && data.capabilities && data.cases.length, "target identity page");
      completed = targetState.cases.find((item) => Number(item.id) === Number(runtime.identity.case_id));
      if (!completed || completed.status !== "COMPLETED" || completed.approval_count !== 2) throw new Error("completed identity case not shown");
      await miniProgram.screenshot({ path: targetScreenshot });
    }

    const reviewerScreenshot = path.join(__dirname, "02-reviewer-completed-identity.png");
    let reviewed;
    if (fs.existsSync(reviewerScreenshot)) {
      reviewed = { id: runtime.identity.case_id, status: runtime.identity.final_status, approval_count: 2 };
    } else {
      const reviewer = await smsLogin(miniProgram, "reviewer1");
      if (!reviewer.state.canReviewIdentity) throw new Error("reviewer home did not expose identity review");
      page = await tapTo(miniProgram, await buttonUrl(reviewer.page, "/pages/supervisor/identity-recovery"), "pages/supervisor/identity-recovery");
      const reviewState = await waitData(page, (data) => !data.loading && data.items.length, "reviewer identity page");
      reviewed = reviewState.items.find((item) => Number(item.id) === Number(runtime.identity.case_id));
      if (!reviewed || reviewed.status !== "COMPLETED" || reviewed.approval_count !== 2) throw new Error("review page did not show completed case");
      await miniProgram.screenshot({ path: reviewerScreenshot });
    }

    const current = await miniProgram.currentPage();
    if (current && current.path === "pages/supervisor/sla") {
      page = current;
    } else {
      const admin = await smsLogin(miniProgram, "admin");
      if (!admin.state.canManageSla) throw new Error("tenant admin home did not expose SLA management");
      page = await tapTo(miniProgram, await buttonUrl(admin.page, "/pages/supervisor/sla"), "pages/supervisor/sla");
    }
    let slaState = await waitData(page, (data) => !data.loading && data.versioningAvailable && data.items.length, "SLA page");
    const categoryIndex = slaState.categoryOptions.findIndex((item) => item.value === "OTHER");
    const priorityIndex = slaState.priorityOptions.findIndex((item) => item.value === "LOW");
    await tolerateTimeout(() => pickerByPrefix(page, "工单类型：").then((item) => item.trigger("change", { value: categoryIndex })));
    await waitData(page, (data) => Number(data.categoryIndex) === categoryIndex, "SLA category selection");
    await tolerateTimeout(() => pickerByPrefix(page, "优先级：").then((item) => item.trigger("change", { value: priorityIndex })));
    slaState = await waitData(page, (data) => Number(data.priorityIndex) === priorityIndex, "SLA priority selection");
    const policy = slaState.items.find((item) => Number(item.id) === Number(runtime.sla.policy_id));
    if (!policy || policy.version_no !== 2 || policy.accept_minutes !== 11 || policy.respond_minutes !== 23 || policy.complete_minutes !== 59) throw new Error("SLA page does not reflect version two");
    await tolerateTimeout(() => button(page, "刷新服务端策略").then((item) => item.tap()));
    await waitData(page, (data) => !data.loading && !data.error, "SLA refresh");
    await miniProgram.screenshot({ path: path.join(__dirname, "03-sla-version2.png") });

    const evidence = {
      task_id: "PRODUCTION-V1-3317-IDENTITY-SLA-PAGES",
      run_id: privateRun.run_id,
      environment: { api: "127.0.0.1:8083", database: "127.0.0.1:3317/property_ai_auth001_test@0014", automator: WS },
      identity: { case_id: completed.id, target_status: completed.status, target_approval_count: completed.approval_count, reviewer_status: reviewed.status, reviewer_approval_count: reviewed.approval_count },
      sla: { policy_id: policy.id, version_no: policy.version_no, accept_minutes: policy.accept_minutes, respond_minutes: policy.respond_minutes, complete_minutes: policy.complete_minutes, versioning_available: slaState.versioningAvailable },
      checks: { target_password_login: true, profile_navigation: true, completed_case_visible_to_target: true, completed_case_visible_to_reviewer: true, tenant_admin_sla_scope: true, sla_version2_visible_after_picker_interaction: true },
      requests: requestEvidence(offset),
      screenshots: ["01-target-completed-identity.png", "02-reviewer-completed-identity.png", "03-sla-version2.png"],
      boundaries: { development_sms_used: true, seed_login_used: false, dev_auth_used: false, page_state_faked: false, page_business_write: false, identity_screenshots_reused_from_same_collector_attempts: true, real_identity_proof_claimed: false, model_requests: 0 },
    };
    fs.writeFileSync(OUTPUT, JSON.stringify(evidence, null, 2));
    console.log(JSON.stringify({ case_id: completed.id, policy_id: policy.id, version: policy.version_no, requests: evidence.requests.length }));
  } finally {
    await miniProgram.disconnect();
  }
})().catch((error) => {
  let message = error && error.stack ? error.stack : String(error);
  if (privateRun) {
    for (const value of Object.values(privateRun.phones || {})) message = message.split(String(value)).join("[REDACTED_PHONE]");
    for (const value of Object.values(privateRun.passwords || {})) message = message.split(String(value)).join("[REDACTED_PASSWORD]");
  }
  console.error(message);
  process.exit(1);
});
