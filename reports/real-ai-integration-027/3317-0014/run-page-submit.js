"use strict";

const crypto = require("crypto");
const fs = require("fs");
const http = require("http");
const path = require("path");
const automator = require(process.env.MP_AUTOMATOR_MODULE);

const ROOT = path.resolve(__dirname, "..", "..", "..");
const CONTEXT = process.env.AUTH001_PAGE_SUBMIT_CONTEXT;
const API_LOG = process.env.AUTH001_API_LOG;
const WS = process.env.MP_AUTOMATOR_WS || "ws://127.0.0.1:9440";
const OUTPUT = process.env.AUTH001_PAGE_SUBMIT_EVIDENCE || path.join(__dirname, "page-submit-evidence.json");
const PHASE = process.argv[2];
const SLA_VALUES = Object.freeze({
  accept: Number(process.env.AUTH001_SLA_ACCEPT_MINUTES || 13),
  respond: Number(process.env.AUTH001_SLA_RESPOND_MINUTES || 29),
  complete: Number(process.env.AUTH001_SLA_COMPLETE_MINUTES || 61),
  warning: Number(process.env.AUTH001_SLA_WARNING_MINUTES || 7),
  escalation: Number(process.env.AUTH001_SLA_ESCALATION_MINUTES || 13),
});
let privateRun;
const captureFailures = [];

function wait(ms) { return new Promise((resolve) => setTimeout(resolve, ms)); }
function hash(value) { return crypto.createHash("sha256").update(String(value)).digest("hex").slice(0, 16); }

async function tolerateTimeout(operation) {
  try { return await operation(); }
  catch (error) {
    if (!String(error && error.message).includes("timeout waiting for automator response")) throw error;
    await wait(500);
    return null;
  }
}

async function capture(miniProgram, target) {
  await tolerateTimeout(() => miniProgram.screenshot({ path: target }));
  await wait(500);
  if (!fs.existsSync(target) || fs.statSync(target).size === 0) {
    captureFailures.push(path.basename(target));
    return false;
  }
  return true;
}

async function current(miniProgram) { return miniProgram.currentPage(); }

async function waitPath(miniProgram, expected) {
  for (let attempt = 0; attempt < 100; attempt += 1) {
    const page = await current(miniProgram);
    if (page && page.path === expected) return page;
    await wait(250);
  }
  const page = await current(miniProgram);
  throw new Error(`expected ${expected}; current=${page && page.path}`);
}

async function waitData(page, predicate, label) {
  for (let attempt = 0; attempt < 120; attempt += 1) {
    const data = await page.data();
    if (predicate(data)) return data;
    await wait(250);
  }
  throw new Error(`${label} timed out`);
}

async function byText(page, selector, text) {
  for (const item of await page.$$(selector)) {
    const value = ((await item.text()) || "").replace(/\s+/g, " ").trim();
    if (value.includes(text)) return item;
  }
  throw new Error(`${selector} text not found: ${text}`);
}

async function buttonUrl(page, url) {
  for (const item of await page.$$("button")) {
    if ((await item.attribute("data-url")) === url) return item;
  }
  throw new Error(`navigation button not found: ${url}`);
}

async function inputPlaceholder(page, placeholder, value) {
  for (const item of await page.$$("input")) {
    if ((await item.attribute("placeholder")) === placeholder) {
      await item.input(String(value));
      return;
    }
  }
  throw new Error(`input not found: ${placeholder}`);
}

async function inputField(page, field, value) {
  for (const item of await page.$$("input")) {
    if ((await item.attribute("data-field")) === field) {
      await item.input(String(value));
      return;
    }
  }
  throw new Error(`input data-field not found: ${field}`);
}

async function tapTo(miniProgram, element, expected) {
  await tolerateTimeout(() => element.tap());
  return waitPath(miniProgram, expected);
}

async function reset(miniProgram) {
  await miniProgram.callWxMethod("clearStorageSync");
  await miniProgram.callWxMethod("reLaunch", { url: "/pages/login/login" });
  return waitPath(miniProgram, "pages/login/login");
}

async function passwordLogin(miniProgram, role, expectSuccess = true) {
  let page = await reset(miniProgram);
  await inputPlaceholder(page, "手机号", privateRun.phones[role]);
  await inputPlaceholder(page, "密码", privateRun.passwords[role]);
  await tolerateTimeout(() => byText(page, "button", "登录").then((item) => item.tap()));
  if (!expectSuccess) {
    const state = await waitData(page, (data) => !data.loading && data.error, `${role} rejected login`);
    return { page, state };
  }
  page = await waitPath(miniProgram, "pages/home/home");
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
  await tolerateTimeout(() => byText(page, ".link-text", "验证码登录").then((item) => item.tap()));
  await waitData(page, (data) => data.mode === "sms", `${role} sms mode`);
  await inputPlaceholder(page, "手机号", privateRun.phones[role]);
  await tolerateTimeout(() => byText(page, "button", "获取验证码").then((item) => item.tap()));
  let state = await waitData(page, (data) => data.challengePrompt && !data.loading, `${role} challenge`);
  await inputPlaceholder(page, "挑战答案", answer(state.challengePrompt));
  await tolerateTimeout(() => byText(page, "button", "获取验证码").then((item) => item.tap()));
  await waitData(page, (data) => !data.challengePrompt && data.cooldown > 0 && !data.loading, `${role} delivery`);
  await inputPlaceholder(page, "短信验证码", await testInbox(privateRun.phones[role]));
  await tolerateTimeout(() => byText(page, "button", "登录").then((item) => item.tap()));
  page = await waitPath(miniProgram, "pages/home/home");
  state = await waitData(page, (data) => data.me && !data.loading, `${role} home`);
  return { page, state };
}

async function openIdentity(miniProgram, loginPage) {
  let page = await tapTo(miniProgram, await buttonUrl(loginPage, "/pages/profile/profile"), "pages/profile/profile");
  page = await tapTo(miniProgram, await byText(page, "button", "账号合并 / 高风险恢复"), "pages/identity-recovery/identity-recovery");
  await waitData(page, (data) => !data.loading && data.capabilities && data.capabilities.enabled, "identity capabilities");
  return page;
}

async function openReview(miniProgram, home) {
  return tapTo(miniProgram, await buttonUrl(home, "/pages/supervisor/identity-recovery"), "pages/supervisor/identity-recovery");
}

async function reasonAndReview(page, caseId, reason) {
  for (const item of await page.$$("input")) {
    if (Number(await item.attribute("data-id")) === Number(caseId)) {
      await item.input(reason);
      break;
    }
  }
  for (const item of await page.$$("button")) {
    if (Number(await item.attribute("data-id")) === Number(caseId) && ((await item.text()) || "").includes("批准")) {
      await tolerateTimeout(() => item.tap());
      return;
    }
  }
  throw new Error(`review controls not found for case ${caseId}`);
}

function loadEvidence() {
  if (!fs.existsSync(OUTPUT)) {
    return {
      task_id: "PRODUCTION-V1-3317-IDENTITY-SLA-PAGE-SUBMIT",
      run_id: privateRun.run_id,
      environment: { api: "127.0.0.1:8083", database: "127.0.0.1:3317/property_ai_auth001_test@0014", automator: WS },
      phases: {}, requests: [], screenshots: [],
      boundaries: { development_sms: true, isolated_test_proof: true, seed_login: false, dev_auth: false, page_state_faked: false, business_sql: false, model_requests: 0 },
    };
  }
  return JSON.parse(fs.readFileSync(OUTPUT, "utf8"));
}

function requestEvidence(offset) {
  const lines = fs.readFileSync(API_LOG).subarray(offset).toString("utf8").split(/\r?\n/).filter(Boolean);
  return lines.map((line) => { try { return JSON.parse(line); } catch (_) { return null; } })
    .filter((item) => item && item.event === "request_completed")
    .map((item) => ({ method: item.method, path: item.path, status: item.status_code, request_id_hash: hash(item.request_id) }));
}

function save(privateChanged, evidence) {
  evidence.screenshots = evidence.screenshots.filter((name) => fs.existsSync(path.join(__dirname, name)));
  evidence.screenshot_capture_failures = Array.from(new Set([...(evidence.screenshot_capture_failures || []), ...captureFailures]));
  if (privateChanged) fs.writeFileSync(CONTEXT, JSON.stringify(privateRun, null, 2));
  fs.writeFileSync(OUTPUT, JSON.stringify(evidence, null, 2));
}

async function phasePast(miniProgram, evidence) {
  const submittedShot = path.join(__dirname, `${privateRun.run_id}-04-identity-page-submitted.png`);
  if (fs.existsSync(OUTPUT) && fs.existsSync(submittedShot) && !privateRun.case_id) {
    const recoveryReviewer = await smsLogin(miniProgram, "reviewer1");
    const recoveryPage = await openReview(miniProgram, recoveryReviewer.page);
    const recoveryState = await waitData(recoveryPage, (data) => !data.loading && data.items.length, "recover reviewed case");
    const matches = recoveryState.items.filter((item) =>
      Number(item.source_user_id) === Number(privateRun.user_ids.source) &&
      item.status === "PENDING_REVIEW" && item.approval_count === 2 && item.version === 3
    );
    if (matches.length !== 1) throw new Error(`expected one recoverable reviewed case, found ${matches.length}`);
    privateRun.case_id = Number(matches[0].id);
    const reviewedShot = path.join(__dirname, `${privateRun.run_id}-05-identity-two-page-reviews.png`);
    await capture(miniProgram, reviewedShot);
    evidence.phases.past_clock_submit_and_reviews = { case_id: privateRun.case_id, status: "PENDING_REVIEW", approval_count: 2, version: 3, clock: privateRun.past_clock_utc, resumed_after_evidence_writer_exit: true };
    evidence.screenshots.push(path.basename(submittedShot), path.basename(reviewedShot));
    save(true, evidence);
    return;
  }
  const target = await passwordLogin(miniProgram, "target");
  privateRun.target_page_session = await miniProgram.evaluate(() => ({
    access_token: wx.getStorageSync("access_token") || "",
    refresh_token: wx.getStorageSync("refresh_token") || "",
  }));
  if (!privateRun.target_page_session.access_token || !privateRun.target_page_session.refresh_token) {
    throw new Error("target page session was not captured before finalization");
  }
  fs.writeFileSync(CONTEXT, JSON.stringify(privateRun, null, 2));
  let page = await openIdentity(miniProgram, target.page);
  await inputPlaceholder(page, "待核验联系电话", privateRun.phones.source);
  await inputPlaceholder(page, "核验渠道一次性令牌", privateRun.proof_assertion);
  await tolerateTimeout(() => byText(page, "button", "提交核验申请").then((item) => item.tap()));
  await waitData(page, (data) => !data.loading && String(data.notice || "").includes("已进入隔离与双人审核"), "identity submit");
  await capture(miniProgram, submittedShot);

  const reviewer1 = await smsLogin(miniProgram, "reviewer1");
  page = await openReview(miniProgram, reviewer1.page);
  let state = await waitData(page, (data) => !data.loading && data.items.length, "reviewer1 cases");
  const candidates = state.items.filter((item) => Number(item.source_user_id) === Number(privateRun.user_ids.source) && item.status === "PENDING_REVIEW");
  if (candidates.length !== 1) throw new Error(`expected one new identity case, found ${candidates.length}`);
  privateRun.case_id = Number(candidates[0].id);
  await reasonAndReview(page, privateRun.case_id, `${privateRun.run_id} 独立审核一`);
  await waitData(page, (data) => data.items.some((item) => Number(item.id) === privateRun.case_id && item.approval_count === 1 && item.version === 2), "review one");

  const reviewer2 = await smsLogin(miniProgram, "reviewer2");
  page = await openReview(miniProgram, reviewer2.page);
  await waitData(page, (data) => !data.loading && data.items.some((item) => Number(item.id) === privateRun.case_id && item.approval_count === 1), "reviewer2 case");
  await reasonAndReview(page, privateRun.case_id, `${privateRun.run_id} 独立审核二`);
  state = await waitData(page, (data) => data.items.some((item) => Number(item.id) === privateRun.case_id && item.approval_count === 2 && item.version === 3), "review two");
  const reviewedShot = path.join(__dirname, `${privateRun.run_id}-05-identity-two-page-reviews.png`);
  await capture(miniProgram, reviewedShot);
  evidence.phases.past_clock_submit_and_reviews = { case_id: privateRun.case_id, status: "PENDING_REVIEW", approval_count: 2, version: 3, clock: privateRun.past_clock_utc };
  evidence.screenshots.push(path.basename(submittedShot), path.basename(reviewedShot));
  save(true, evidence);
}

async function phaseCurrent(miniProgram, evidence) {
  if (!privateRun.case_id) throw new Error("past phase did not record a case id");
  if (!privateRun.target_page_session || !privateRun.target_page_session.access_token || !privateRun.target_page_session.refresh_token) {
    throw new Error("target pre-finalization page session is unavailable");
  }
  const reviewer2 = await smsLogin(miniProgram, "reviewer2");
  let page = await openReview(miniProgram, reviewer2.page);
  let state = await waitData(page, (data) => !data.loading && data.items.some((item) => Number(item.id) === privateRun.case_id), "finalize case");
  const currentCase = state.items.find((item) => Number(item.id) === privateRun.case_id);
  if (currentCase.status === "PENDING_REVIEW" && currentCase.version === 3) {
    for (const item of await page.$$("button")) {
      if (Number(await item.attribute("data-id")) === privateRun.case_id && ((await item.text()) || "").includes("确认审核完成")) {
        await tolerateTimeout(() => item.tap());
        break;
      }
    }
    state = await waitData(page, (data) => data.items.some((item) => Number(item.id) === privateRun.case_id && item.status === "APPROVED" && item.version === 4), "finalized case");
  } else if (currentCase.status !== "APPROVED" || currentCase.version !== 4) {
    throw new Error(`identity case cannot resume current phase: ${currentCase.status}/${currentCase.version}`);
  }
  const approvedShot = path.join(__dirname, `${privateRun.run_id}-06-identity-page-finalized.png`);
  await capture(miniProgram, approvedShot);

  await miniProgram.evaluate((session) => {
    wx.clearStorageSync();
    wx.setStorageSync("access_token", session.access_token);
    wx.setStorageSync("refresh_token", session.refresh_token);
  }, privateRun.target_page_session);
  await miniProgram.callWxMethod("reLaunch", { url: "/pages/identity-recovery/identity-recovery" });
  page = await waitPath(miniProgram, "pages/identity-recovery/identity-recovery");
  await waitData(page, (data) => !data.loading && data.cases.some((item) => Number(item.id) === privateRun.case_id && item.status === "APPROVED"), "target approved case");
  await inputPlaceholder(page, "目标账号当前密码（如已有）", privateRun.passwords.target);
  const readyShot = path.join(__dirname, `${privateRun.run_id}-07-identity-page-completion-ready.png`);
  await capture(miniProgram, readyShot);
  for (const item of await page.$$("button")) {
    if (Number(await item.attribute("data-id")) === privateRun.case_id && ((await item.text()) || "").includes("确认并执行身份事务")) {
      await tolerateTimeout(() => item.tap());
      break;
    }
  }
  await waitPath(miniProgram, "pages/login/login");
  const sourceRejected = await passwordLogin(miniProgram, "source", false);
  const rejectShot = path.join(__dirname, `${privateRun.run_id}-08-merged-source-password-rejected.png`);
  await capture(miniProgram, rejectShot);
  if (!sourceRejected.state.error) throw new Error("merged source password was not rejected");
  await passwordLogin(miniProgram, "target");
  const freshShot = path.join(__dirname, `${privateRun.run_id}-09-target-fresh-login.png`);
  await capture(miniProgram, freshShot);
  evidence.phases.current_clock_finalize_and_complete = { case_id: privateRun.case_id, final_status: "COMPLETED", source_old_password_rejected: true, target_fresh_password_login: true };
  evidence.screenshots.push(path.basename(approvedShot), path.basename(readyShot), path.basename(rejectShot), path.basename(freshShot));
  save(false, evidence);
}

async function phaseSla(miniProgram, evidence) {
  if (
    !Object.values(SLA_VALUES).every(Number.isInteger) ||
    SLA_VALUES.accept < 1 ||
    SLA_VALUES.respond < SLA_VALUES.accept ||
    SLA_VALUES.complete < SLA_VALUES.respond ||
    SLA_VALUES.warning < 0 ||
    SLA_VALUES.escalation < 0
  ) throw new Error("invalid bounded SLA values");
  const admin = await smsLogin(miniProgram, "admin");
  let page = await tapTo(miniProgram, await buttonUrl(admin.page, "/pages/supervisor/sla"), "pages/supervisor/sla");
  let state = await waitData(page, (data) => !data.loading && data.versioningAvailable && data.items.length, "SLA editor");
  const categoryIndex = state.categoryOptions.findIndex((item) => item.value === "OTHER");
  // The resident create page deliberately does not ask residents to classify
  // urgency. The create API therefore applies the frozen MEDIUM default until
  // analysis. Exercise the policy key that the actual page request sends.
  const priorityIndex = state.priorityOptions.findIndex((item) => item.value === "MEDIUM");
  const pickers = await page.$$("picker");
  for (const picker of pickers) {
    const text = ((await picker.text()) || "").trim();
    if (text.startsWith("工单类型：")) await tolerateTimeout(() => picker.trigger("change", { value: categoryIndex }));
    if (text.startsWith("优先级：")) await tolerateTimeout(() => picker.trigger("change", { value: priorityIndex }));
  }
  await waitData(page, (data) => data.categoryIndex === categoryIndex && data.priorityIndex === priorityIndex, "SLA selection");
  await inputField(page, "acceptMinutes", SLA_VALUES.accept);
  await inputField(page, "respondMinutes", SLA_VALUES.respond);
  await inputField(page, "completeMinutes", SLA_VALUES.complete);
  await inputField(page, "warningLeadMinutes", SLA_VALUES.warning);
  await inputField(page, "escalationMinutes", SLA_VALUES.escalation);
  await inputField(page, "changeReason", `${privateRun.run_id} 页面提交验收`);
  await tolerateTimeout(() => byText(page, "button", "保存并重新读取").then((item) => item.tap()));
  state = await waitData(page, (data) => !data.loading && String(data.notice || "").includes("已保存"), "SLA page save");
  const policy = state.items.find((item) => item.category === "OTHER" && item.priority === "MEDIUM" && Number(item.property_id) === Number(state.propertyId));
  if (
    !policy ||
    policy.accept_minutes !== SLA_VALUES.accept ||
    policy.respond_minutes !== SLA_VALUES.respond ||
    policy.complete_minutes !== SLA_VALUES.complete ||
    policy.warning_lead_minutes !== SLA_VALUES.warning ||
    policy.escalation_minutes !== SLA_VALUES.escalation
  ) throw new Error("saved SLA policy not reflected on page");
  privateRun.sla_policy_id = Number(policy.id);
  const slaShot = path.join(__dirname, `${privateRun.run_id}-sla-fix-10-sla-page-submitted.png`);
  await capture(miniProgram, slaShot);

  const resident = await smsLogin(miniProgram, "resident");
  page = await tapTo(miniProgram, await buttonUrl(resident.page, "/pages/work-orders/create"), "pages/work-orders/create");
  state = await waitData(page, (data) => !data.houseLoading && data.houses.length && data.contactPhone, "work order form");
  const firstHouse = await page.$("button.house-choice");
  if (!firstHouse) throw new Error("resident has no page-selectable house");
  await tolerateTimeout(() => firstHouse.tap());
  const textarea = await page.$("textarea");
  await textarea.input(`${privateRun.run_id} 页面提交普通墙面渗水，验证不可变SLA快照`);
  const checkboxGroup = await page.$("checkbox-group");
  await tolerateTimeout(() => checkboxGroup.trigger("change", { value: ["confirmed"] }));
  await waitData(page, (data) => data.houseId && data.contactConfirmed && data.text.includes(privateRun.run_id), "work order ready");
  await tolerateTimeout(() => byText(page, "button", "提交报修").then((item) => item.tap()));
  page = await waitPath(miniProgram, "pages/work-orders/detail");
  state = await waitData(page, (data) => !data.loading && data.workOrder && data.workOrder.id, "work order detail");
  privateRun.work_order_id = Number(state.workOrder.id);
  const orderShot = path.join(__dirname, `${privateRun.run_id}-sla-fix-11-sla-work-order-page-submitted.png`);
  await capture(miniProgram, orderShot);
  evidence.phases.sla_policy_and_work_order_submit = {
    policy_id: privateRun.sla_policy_id,
    policy_version: policy.version_no,
    work_order_id: privateRun.work_order_id,
    work_order_status: state.workOrder.status,
    expected_snapshot: SLA_VALUES,
    enqueue_expected: false,
  };
  evidence.screenshots.push(path.basename(slaShot), path.basename(orderShot));
  save(true, evidence);
}

(async () => {
  if (!["past", "current", "sla"].includes(PHASE)) throw new Error("usage: run-page-submit.js {past|current|sla}");
  if (!CONTEXT || !fs.existsSync(CONTEXT)) throw new Error("private page-submit context missing");
  if (!API_LOG || !fs.existsSync(API_LOG)) throw new Error("API log missing");
  privateRun = JSON.parse(fs.readFileSync(CONTEXT, "utf8"));
  const evidence = loadEvidence();
  const phaseKey = { past: "past_clock_submit_and_reviews", current: "current_clock_finalize_and_complete", sla: "sla_policy_and_work_order_submit" }[PHASE];
  if (evidence.phases[phaseKey]) throw new Error(`phase already completed: ${PHASE}`);
  const offset = fs.statSync(API_LOG).size;
  const miniProgram = await automator.connect({ wsEndpoint: WS });
  try {
    if (PHASE === "past") await phasePast(miniProgram, evidence);
    if (PHASE === "current") await phaseCurrent(miniProgram, evidence);
    if (PHASE === "sla") await phaseSla(miniProgram, evidence);
    evidence.requests = evidence.requests.concat(requestEvidence(offset));
    evidence.last_updated_utc = new Date().toISOString();
    save(false, evidence);
    console.log(JSON.stringify({ phase: PHASE, run_id: privateRun.run_id, case_id: privateRun.case_id, policy_id: privateRun.sla_policy_id, work_order_id: privateRun.work_order_id }));
  } finally {
    await miniProgram.disconnect();
  }
})().catch((error) => {
  let message = error && error.stack ? error.stack : String(error);
  if (privateRun) {
    for (const value of Object.values(privateRun.phones || {})) message = message.split(String(value)).join("[REDACTED_PHONE]");
    for (const value of Object.values(privateRun.passwords || {})) message = message.split(String(value)).join("[REDACTED_PASSWORD]");
    if (privateRun.proof_assertion) message = message.split(String(privateRun.proof_assertion)).join("[REDACTED_PROOF]");
  }
  console.error(message.replace(/eyJ[\w-]+\.[\w-]+\.[\w-]+/g, "[REDACTED_TOKEN]"));
  process.exit(1);
});
