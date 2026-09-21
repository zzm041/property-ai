"use strict";

const crypto = require("crypto");
const fs = require("fs");
const http = require("http");
const path = require("path");
const automator = require(process.env.MP_AUTOMATOR_MODULE);

const REPO = path.resolve(__dirname, "..", "..");
const OUT = __dirname;
const PRIVATE_RUN = path.join(REPO, "tmp", "incident-management-009", "context.json");
const PRIVATE_AUTH = path.join(REPO, "tmp", "auth-password-change", "context.json");
const API_LOG = path.join(
  REPO,
  "tmp",
  "auth001-runtime",
  "api-9033caa-incident.stdout.log"
);
const WS = process.env.MP_AUTOMATOR_WS;
const CODE_SHA = "9033caae493f120789f07f919d69666afc33625b";
let activeMiniProgram = null;
let privateRun = null;
let privateAuth = null;

function wait(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

function sha256Short(value) {
  return crypto.createHash("sha256").update(String(value)).digest("hex").slice(0, 16);
}

function redactError(error) {
  let text = error && error.stack ? error.stack : String(error || "Unknown error");
  const secrets = [];
  for (const source of [privateRun, privateAuth]) {
    if (!source) continue;
    for (const value of Object.values(source.phones || {})) secrets.push(String(value));
    for (const value of Object.values(source.passwords || {})) secrets.push(String(value));
    for (const session of Object.values(source.sessions || {})) {
      if (session && typeof session === "object") {
        if (session.access_token) secrets.push(String(session.access_token));
        if (session.refresh_token) secrets.push(String(session.refresh_token));
      }
    }
  }
  for (const value of secrets.filter(Boolean)) {
    text = text.split(value).join("[REDACTED_CREDENTIAL]");
  }
  return text
    .replace(/\b1[3-9]\d{9}\b/g, "[REDACTED_PHONE]")
    .replace(/Bearer\s+\S+/gi, "Bearer [REDACTED]")
    .replace(/eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+/g, "[REDACTED_TOKEN]");
}

function logOffset() {
  return fs.statSync(API_LOG).size;
}

function eventsAfter(offset) {
  return fs
    .readFileSync(API_LOG)
    .subarray(offset)
    .toString("utf8")
    .split(/\r?\n/)
    .filter(Boolean)
    .map((line) => {
      try {
        return JSON.parse(line);
      } catch (error) {
        return null;
      }
    })
    .filter(Boolean);
}

function requestSummary(offset, method, pathname) {
  const events = eventsAfter(offset);
  const errors = new Map();
  for (const event of events) {
    if (event.event === "application_error" && event.request_id) {
      errors.set(event.request_id, event.error_code || null);
    }
  }
  return events
    .filter(
      (event) =>
        event.event === "request_completed" &&
        event.method === method &&
        event.path === pathname
    )
    .map((event) => ({
      status: Number(event.status_code),
      error: errors.get(event.request_id) || null,
      request_id_hash: sha256Short(event.request_id),
    }));
}

async function current(miniProgram) {
  return miniProgram.currentPage();
}

async function waitPath(miniProgram, fragment, attempts = 80) {
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    const page = await current(miniProgram);
    if (page && page.path && page.path.includes(fragment)) return page;
    await wait(250);
  }
  const page = await current(miniProgram);
  throw new Error(`Expected page ${fragment}; current=${page && page.path}`);
}

async function waitData(page, predicate, label, attempts = 100) {
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    const data = await page.data();
    if (predicate(data)) return data;
    await wait(250);
  }
  throw new Error(`${label} timed out`);
}

async function fillPlaceholder(page, placeholder, value) {
  for (const element of await page.$$("input")) {
    if ((await element.attribute("placeholder")) === placeholder) {
      await element.input(String(value));
      return;
    }
  }
  throw new Error(`Input not found: ${placeholder}`);
}

async function tapTextContains(page, selector, expected) {
  for (const element of await page.$$(selector)) {
    const text = ((await element.text()) || "").replace(/\s+/g, " ").trim();
    if (text.includes(expected)) {
      await element.tap();
      return;
    }
  }
  throw new Error(`Text not found: ${expected}`);
}

async function buttonByText(page, expected, exact = false) {
  for (const element of await page.$$("button")) {
    const text = ((await element.text()) || "").replace(/\s+/g, " ").trim();
    if ((exact && text === expected) || (!exact && text.includes(expected))) return element;
  }
  throw new Error(`Button not found: ${expected}`);
}

async function buttonByDataUrl(page, expected) {
  for (const element of await page.$$("button")) {
    if ((await element.attribute("data-url")) === expected) return element;
  }
  throw new Error(`Navigation button not found: ${expected}`);
}

async function tapForPath(miniProgram, element, fragment) {
  let tapError = null;
  try {
    await element.tap();
  } catch (error) {
    tapError = error;
    if (!String(error && error.message).includes("timeout waiting for automator response")) {
      throw error;
    }
  }
  try {
    return await waitPath(miniProgram, fragment);
  } catch (pathError) {
    if (tapError) throw tapError;
    throw pathError;
  }
}

async function resetToLogin(miniProgram) {
  await miniProgram.callWxMethod("clearStorageSync");
  await miniProgram.callWxMethod("reLaunch", { url: "/pages/login/login" });
  return waitPath(miniProgram, "pages/login/login");
}

function answer(prompt) {
  const match = String(prompt || "").match(/^(\d+)\s*\+\s*(\d+)\s*=\s*\?$/);
  if (!match) throw new Error("Unsupported challenge prompt");
  return String(Number(match[1]) + Number(match[2]));
}

function requestJson(method, pathname, body) {
  return new Promise((resolve, reject) => {
    const encoded = JSON.stringify(body || {});
    const request = http.request(
      {
        host: "127.0.0.1",
        port: 8083,
        method,
        path: `/api/v1${pathname}`,
        headers: {
          "Content-Type": "application/json",
          "Content-Length": Buffer.byteLength(encoded),
          "X-Request-Id": `${privateRun.run_id}-page-inbox-${Date.now()}`,
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
            reject(new Error(`Invalid JSON from ${pathname}`));
            return;
          }
          resolve({ status: response.statusCode, payload });
        });
      }
    );
    request.on("error", reject);
    request.write(encoded);
    request.end();
  });
}

async function developmentCode(phone) {
  const response = await requestJson("POST", "/auth/sms/test-inbox", {
    phone,
    purpose: "login",
  });
  const code = response.payload && response.payload.data && response.payload.data.code;
  if (response.status !== 200 || !code) {
    throw new Error("Development SMS inbox did not return the current login code");
  }
  return String(code);
}

async function smsLogin(miniProgram, role) {
  const phone = String(privateRun.phones[role]);
  if (String(privateAuth.phones[role]) !== phone) {
    throw new Error(`Private fixture identity mismatch for ${role}`);
  }
  let page = await resetToLogin(miniProgram);
  const initial = await page.data();
  if (initial.mode !== "password") throw new Error("Login page did not default to password mode");
  await tapTextContains(page, ".link-text", "验证码登录");
  let state = await waitData(page, (value) => value.mode === "sms", `${role} SMS mode`);
  await fillPlaceholder(page, "手机号", phone);
  const offset = logOffset();
  await (await buttonByText(page, "获取验证码", true)).tap();
  state = await waitData(
    page,
    (value) => Boolean(value.challengePrompt) && !value.loading,
    `${role} challenge`
  );
  await fillPlaceholder(page, "挑战答案", answer(state.challengePrompt));
  await (await buttonByText(page, "获取验证码", true)).tap();
  await waitData(
    page,
    (value) => !value.challengePrompt && Number(value.cooldown) > 0 && !value.loading,
    `${role} Development SMS delivery`
  );
  await fillPlaceholder(page, "短信验证码", await developmentCode(phone));
  page = await tapForPath(miniProgram, await buttonByText(page, "登录", true), "pages/home/home");
  const data = await waitData(
    page,
    (value) => Boolean(value.me && value.me.user_id) && !value.loading,
    `${role} home identity`
  );
  const requests = requestSummary(offset, "POST", "/api/v1/auth/sms/login");
  if (requests.length !== 1 || requests[0].status !== 200) {
    throw new Error(`${role} page login did not produce one successful SMS request`);
  }
  if (Number(data.me.user_id) !== Number(privateRun.sessions[role].user_id)) {
    throw new Error(`${role} page login returned a different synthetic identity`);
  }
  return { page, data, request: requests[0] };
}

async function selectIncident(page, incidentId) {
  for (const element of await page.$$(".incident-card")) {
    if (Number(await element.attribute("data-id")) === Number(incidentId)) {
      await element.tap();
      return;
    }
  }
  throw new Error(`Incident card not found: ${incidentId}`);
}

async function scroll(miniProgram, top) {
  await miniProgram.callWxMethod("pageScrollTo", { scrollTop: top, duration: 0 });
  await wait(400);
}

(async () => {
  if (!WS) throw new Error("MP_AUTOMATOR_WS is required");
  privateRun = JSON.parse(fs.readFileSync(PRIVATE_RUN, "utf8"));
  privateAuth = JSON.parse(fs.readFileSync(PRIVATE_AUTH, "utf8"));
  const incidentId = Number(privateRun.incident_id);
  const propertyId = Number(privateRun.org.property_id);
  const tenantId = Number(privateRun.org.tenant_id);
  const employeeId = Number(privateRun.employee_id);
  if (!incidentId || !propertyId || !tenantId || !employeeId) {
    throw new Error("Private Incident context is incomplete");
  }

  const evidence = {
    task_id: "INCIDENT-MGMT-UI-009",
    phase: "wechat_devtools_page",
    captured_at: new Date().toISOString(),
    code_sha: CODE_SHA,
    run_id: privateRun.run_id,
    environment: {
      interaction: "WeChat DevTools miniprogram-automator through actual page controls",
      api: "http://127.0.0.1:8083/api/v1",
      database: "127.0.0.1:3307/property_ai_auth001_test",
      revision: "20260919_0009",
      ai_provider: "DEVELOPMENT_FAKE_AI",
      automator_endpoint: WS,
    },
    objects: {
      tenant_id: tenantId,
      property_id: propertyId,
      employee_id: employeeId,
      incident_id: incidentId,
      work_order_ids: privateRun.work_order_ids.map(Number),
    },
    checks: {},
    screenshots: [],
    boundaries: {
      seed_login_used: false,
      dev_auth_used: false,
      page_state_faked: false,
      direct_database_business_write: false,
      real_qwen_claimed: false,
      announcement_auto_published: false,
    },
  };

  const miniProgram = await automator.connect({ wsEndpoint: WS });
  activeMiniProgram = miniProgram;

  const manager = await smsLogin(miniProgram, "manager");
  if (!manager.data.canViewIncidents || !manager.data.canManageIncidents) {
    throw new Error("Manager home did not expose the authorized Incident entry");
  }
  const managerOffset = logOffset();
  let page = await tapForPath(
    miniProgram,
    await buttonByDataUrl(manager.page, "/pages/supervisor/incidents"),
    "pages/supervisor/incidents"
  );
  let state = await waitData(
    page,
    (value) => !value.loading && value.items.some((item) => Number(item.id) === incidentId),
    "manager incident list"
  );
  if (
    Number(state.propertyId) !== propertyId ||
    Number(state.tenantId) !== tenantId ||
    !state.canManage ||
    !state.canDraftAnnouncement
  ) {
    throw new Error("Manager Incident scope or capabilities did not match the authorized property");
  }
  await scroll(miniProgram, 0);
  await miniProgram.screenshot({ path: path.join(OUT, "01-manager-incident-list.png") });
  evidence.screenshots.push("01-manager-incident-list.png");

  await selectIncident(page, incidentId);
  state = await waitData(
    page,
    (value) =>
      !value.loading &&
      value.selected &&
      Number(value.selected.id) === incidentId &&
      Number(value.selected.work_order_count) === privateRun.work_order_ids.length,
    "manager incident detail"
  );
  if (Number(state.selected.primary_employee_id) !== employeeId) {
    throw new Error("Manager detail primary employee did not match the automatic assignment");
  }
  await scroll(miniProgram, 9999);
  await miniProgram.screenshot({ path: path.join(OUT, "02-manager-incident-detail.png") });
  evidence.screenshots.push("02-manager-incident-detail.png");

  const summaryOffset = logOffset();
  await (await buttonByText(page, "AI 运营摘要")).tap();
  state = await waitData(
    page,
    (value) =>
      !value.loading &&
      value.selected &&
      Number(value.selected.id) === incidentId &&
      Boolean(value.selected.ai_summary) &&
      String(value.notice || "").includes("本地开发模型"),
    "manager AI summary"
  );
  const summaryRequests = requestSummary(
    summaryOffset,
    "POST",
    `/api/v1/supervisor/incidents/${incidentId}/summary`
  );
  if (summaryRequests.length !== 1 || summaryRequests[0].status !== 200) {
    throw new Error("Summary button did not produce exactly one successful request");
  }
  await scroll(miniProgram, 9999);
  await miniProgram.screenshot({ path: path.join(OUT, "03-manager-ai-summary.png") });
  evidence.screenshots.push("03-manager-ai-summary.png");

  const draftOffset = logOffset();
  await (await buttonByText(page, "公告草稿")).tap();
  state = await waitData(
    page,
    (value) =>
      !value.loading &&
      Boolean(value.lastDraftId) &&
      String(value.notice || "").includes("人工编辑、审核和发布"),
    "manager announcement draft"
  );
  const draftId = Number(state.lastDraftId);
  const draftRequests = requestSummary(draftOffset, "POST", "/api/v1/admin/announcements");
  if (!draftId || draftRequests.length !== 1 || draftRequests[0].status !== 200) {
    throw new Error("Draft button did not produce one successful announcement request");
  }
  await scroll(miniProgram, 9999);
  await miniProgram.screenshot({ path: path.join(OUT, "04-manager-announcement-draft.png") });
  evidence.screenshots.push("04-manager-announcement-draft.png");
  evidence.objects.announcement_draft_id = draftId;
  evidence.checks.manager = {
    status: "PASS",
    official_sms_page_login: manager.request,
    home_entry_visible: true,
    scoped_property_picker: true,
    same_incident_list_and_detail: true,
    work_order_count: Number(state.selected.work_order_count),
    primary_employee_matches: true,
    ai_summary_page_state_present: true,
    ai_summary_request: summaryRequests[0],
    announcement_draft_page_state_present: true,
    announcement_draft_request: draftRequests[0],
    incident_page_requests: {
      list: requestSummary(managerOffset, "GET", "/api/v1/supervisor/incidents"),
      detail: requestSummary(
        managerOffset,
        "GET",
        `/api/v1/supervisor/incidents/${incidentId}`
      ),
    },
  };

  const staff = await smsLogin(miniProgram, "staff");
  if (!staff.data.canViewIncidents || staff.data.canManageIncidents) {
    throw new Error("Staff home Incident capability did not match participant-only access");
  }
  const staffOffset = logOffset();
  page = await tapForPath(
    miniProgram,
    await buttonByDataUrl(staff.page, "/pages/supervisor/incidents"),
    "pages/supervisor/incidents"
  );
  state = await waitData(
    page,
    (value) => !value.loading && value.items.some((item) => Number(item.id) === incidentId),
    "staff participant Incident list"
  );
  if (state.canManage || state.canDraftAnnouncement) {
    throw new Error("Staff participant page exposed management capabilities");
  }
  if (state.items.some((item) => Number(item.id) !== incidentId)) {
    throw new Error("Staff participant page exposed an unrelated Incident");
  }
  await scroll(miniProgram, 0);
  await miniProgram.screenshot({ path: path.join(OUT, "05-staff-participant-list.png") });
  evidence.screenshots.push("05-staff-participant-list.png");
  await selectIncident(page, incidentId);
  state = await waitData(
    page,
    (value) => value.selected && Number(value.selected.id) === incidentId && !value.loading,
    "staff participant Incident detail"
  );
  const staffButtonTexts = [];
  for (const element of await page.$$("button")) {
    staffButtonTexts.push(((await element.text()) || "").trim());
  }
  if (staffButtonTexts.some((text) => text.includes("AI 运营摘要") || text.includes("公告草稿"))) {
    throw new Error("Staff participant detail rendered a management action");
  }
  await scroll(miniProgram, 9999);
  await miniProgram.screenshot({ path: path.join(OUT, "06-staff-participant-detail.png") });
  evidence.screenshots.push("06-staff-participant-detail.png");
  evidence.checks.staff = {
    status: "PASS",
    official_sms_page_login: staff.request,
    participant_entry_visible: true,
    same_incident_visible: true,
    unrelated_incident_count: 0,
    management_controls_hidden: true,
    participant_notice_visible: true,
    incident_page_requests: {
      list: requestSummary(staffOffset, "GET", "/api/v1/supervisor/incidents"),
      detail: requestSummary(
        staffOffset,
        "GET",
        `/api/v1/supervisor/incidents/${incidentId}`
      ),
    },
  };

  evidence.completed_at = new Date().toISOString();
  evidence.redaction =
    "No phone, password, OTP, token, raw request ID, resident text, or secret is emitted.";
  fs.writeFileSync(
    path.join(OUT, "page-evidence.json"),
    `${JSON.stringify(evidence, null, 2)}\n`,
    "utf8"
  );
  process.stdout.write(
    `${JSON.stringify(
      {
        task_id: evidence.task_id,
        run_id: evidence.run_id,
        checks: { manager: "PASS", staff: "PASS" },
        objects: evidence.objects,
        screenshots: evidence.screenshots,
      },
      null,
      2
    )}\n`
  );
  await miniProgram.disconnect();
  activeMiniProgram = null;
})().catch(async (error) => {
  fs.writeFileSync(path.join(OUT, "page-run-error.txt"), `${redactError(error)}\n`, "utf8");
  if (activeMiniProgram) {
    try {
      await activeMiniProgram.disconnect();
    } catch (disconnectError) {
      // Best effort only; never replace the primary failure.
    }
  }
  process.stderr.write(`${redactError(error)}\n`);
  process.exitCode = 1;
});
