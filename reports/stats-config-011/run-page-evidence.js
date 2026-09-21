"use strict";

const crypto = require("crypto");
const fs = require("fs");
const http = require("http");
const path = require("path");
const automator = require(process.env.MP_AUTOMATOR_MODULE);

const REPO = path.resolve(__dirname, "..", "..");
const OUT = __dirname;
const PRIVATE_RUN = path.join(REPO, "tmp", "stats-config-011", "context.json");
const API_EVIDENCE = path.join(OUT, "api-database-evidence.json");
const API_LOG = process.env.MP_API_LOG;
const WS = process.env.MP_AUTOMATOR_WS;
const CODE_SHA = "01785a0a76a3c849f7f4d88fd27fbcb3162929dd";
let activeMiniProgram = null;
let privateRun = null;

function wait(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

async function tolerateAutomatorTimeout(operation) {
  try {
    return await operation();
  } catch (error) {
    if (!String(error && error.message).includes("timeout waiting for automator response")) {
      throw error;
    }
    await wait(400);
    return null;
  }
}

function sha256Short(value) {
  return crypto.createHash("sha256").update(String(value)).digest("hex").slice(0, 16);
}

function redactError(error) {
  let text = error && error.stack ? error.stack : String(error || "Unknown error");
  if (privateRun) {
    for (const value of Object.values(privateRun.phones || {})) {
      text = text.split(String(value)).join("[REDACTED_PHONE]");
    }
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

async function waitPath(miniProgram, fragment, attempts = 100) {
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    const page = await current(miniProgram);
    if (page && page.path && page.path.includes(fragment)) return page;
    await wait(250);
  }
  const page = await current(miniProgram);
  throw new Error(`Expected page ${fragment}; current=${page && page.path}`);
}

async function waitData(page, predicate, label, attempts = 120) {
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

async function buttonByAttribute(page, attribute, expected) {
  for (const element of await page.$$("button")) {
    if ((await element.attribute(attribute)) === expected) return element;
  }
  throw new Error(`Navigation button not found: ${attribute}=${expected}`);
}

async function hasButtonAttribute(page, attribute, expected) {
  for (const element of await page.$$("button")) {
    if ((await element.attribute(attribute)) === expected) return true;
  }
  return false;
}

async function pickerByPrefix(page, prefix) {
  for (const element of await page.$$("picker")) {
    const text = ((await element.text()) || "").replace(/\s+/g, " ").trim();
    if (text.startsWith(prefix)) return element;
  }
  throw new Error(`Picker not found: ${prefix}`);
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
          try {
            resolve({ status: response.statusCode, payload: raw ? JSON.parse(raw) : {} });
          } catch (error) {
            reject(new Error(`Invalid JSON from ${pathname}`));
          }
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
  let page = await resetToLogin(miniProgram);
  const initial = await page.data();
  if (initial.mode !== "password") throw new Error("Login page did not default to password mode");
  await tapTextContains(page, ".link-text", "验证码登录");
  let state = await waitData(page, (value) => value.mode === "sms", `${role} SMS mode`);
  await fillPlaceholder(page, "手机号", phone);
  const offset = logOffset();
  await tolerateAutomatorTimeout(() =>
    buttonByText(page, "获取验证码", true).then((item) => item.tap())
  );
  state = await waitData(
    page,
    (value) => Boolean(value.challengePrompt) && !value.loading,
    `${role} challenge`
  );
  await fillPlaceholder(page, "挑战答案", answer(state.challengePrompt));
  await tolerateAutomatorTimeout(() =>
    buttonByText(page, "获取验证码", true).then((item) => item.tap())
  );
  await waitData(
    page,
    (value) => !value.challengePrompt && Number(value.cooldown) > 0 && !value.loading,
    `${role} Development SMS delivery`
  );
  await fillPlaceholder(page, "短信验证码", await developmentCode(phone));
  page = await tapForPath(
    miniProgram,
    await buttonByText(page, "登录", true),
    "pages/home/home"
  );
  const data = await waitData(
    page,
    (value) => Boolean(value.me && value.me.user_id) && !value.loading,
    `${role} home identity`
  );
  const requests = requestSummary(offset, "POST", "/api/v1/auth/sms/login");
  if (requests.length !== 1 || requests[0].status !== 200) {
    throw new Error(`${role} page login did not produce one successful SMS request`);
  }
  if (Number(data.me.user_id) !== Number(privateRun.identities[role])) {
    throw new Error(`${role} page login returned a different synthetic identity`);
  }
  return { page, data, request: requests[0] };
}

async function scroll(miniProgram, top) {
  await tolerateAutomatorTimeout(() =>
    miniProgram.callWxMethod("pageScrollTo", { scrollTop: top, duration: 0 })
  );
  await wait(700);
}

async function screenshot(miniProgram, name) {
  await wait(1200);
  await miniProgram.screenshot({ path: path.join(OUT, name) });
}

function distribution(items) {
  return Object.fromEntries((items || []).map((item) => [item.code, Number(item.count)]));
}

function assertEqual(actual, expected, label) {
  if (JSON.stringify(actual) !== JSON.stringify(expected)) {
    throw new Error(`${label} did not match the API/database evidence`);
  }
}

function assertSnapshot(overview, expected, scopeType, tenantId, propertyId) {
  if (
    !overview ||
    overview.scope_type !== scopeType ||
    Number(overview.tenant_id) !== Number(tenantId) ||
    (scopeType === "TENANT"
      ? overview.property_id !== null
      : Number(overview.property_id) !== Number(propertyId))
  ) {
    throw new Error(`${scopeType} page snapshot returned the wrong scope`);
  }
  assertEqual(Number(overview.property_count), Number(expected.property_count), "property count");
  assertEqual(
    {
      total: Number(overview.work_orders.total),
      open: Number(overview.work_orders.open),
      terminal: Number(overview.work_orders.terminal),
      by_status: distribution(overview.work_orders.by_status),
      by_category: distribution(overview.work_orders.by_category),
      by_priority: distribution(overview.work_orders.by_priority),
    },
    expected.work_orders,
    "work order snapshot"
  );
  assertEqual(
    {
      total: Number(overview.incidents.total),
      active: Number(overview.incidents.active),
      by_status: distribution(overview.incidents.by_status),
    },
    expected.incidents,
    "incident snapshot"
  );
  assertEqual(
    {
      warning: Number(overview.sla_clocks.warning),
      overdue: Number(overview.sla_clocks.overdue),
      by_state: distribution(overview.sla_clocks.by_state),
    },
    expected.sla_clocks,
    "SLA snapshot"
  );
  const staffing = Object.fromEntries(
    Object.keys(expected.staffing).map((key) => [key, Number(overview.staffing[key])])
  );
  assertEqual(staffing, expected.staffing, "staffing snapshot");
  if (
    !overview.generated_at ||
    !overview.properties ||
    overview.properties.length !== expected.property_count ||
    String(overview.properties[0].business_date) !== String(expected.business_date) ||
    !overview.properties[0].timezone
  ) {
    throw new Error("Page snapshot did not expose generated time and property business date");
  }
}

function assertSuccessfulGets(offset, minimum, label) {
  const requests = requestSummary(offset, "GET", "/api/v1/admin/operations-overview");
  if (requests.length < minimum || requests.some((item) => item.status !== 200)) {
    throw new Error(`${label} did not produce the expected successful overview GET`);
  }
  return requests;
}

(async () => {
  if (!WS) throw new Error("MP_AUTOMATOR_WS is required");
  if (!API_LOG) throw new Error("MP_API_LOG is required");
  privateRun = JSON.parse(fs.readFileSync(PRIVATE_RUN, "utf8"));
  const apiEvidence = JSON.parse(fs.readFileSync(API_EVIDENCE, "utf8"));
  if (apiEvidence.code_sha !== CODE_SHA || privateRun.task_id !== "STATS-CONFIG-UI-011") {
    throw new Error("Stats API/page evidence context does not match the current product code");
  }
  const propertyId = Number(privateRun.org.property_id);
  const tenantId = Number(privateRun.org.tenant_id);
  const expected = apiEvidence.snapshot;
  const evidence = {
    task_id: "STATS-CONFIG-UI-011",
    phase: "wechat_devtools_page_read_only",
    captured_at: new Date().toISOString(),
    code_sha: CODE_SHA,
    run_id: privateRun.run_id,
    environment: {
      interaction: "WeChat DevTools miniprogram-automator through actual page controls",
      api: "http://127.0.0.1:8083/api/v1",
      database: "127.0.0.1:3307/property_ai_auth001_test",
      revision: "20260919_0009",
      provider: "DEVELOPMENT_SMS_AND_FAKE_AI",
      automator_endpoint: WS,
      api_log: path.relative(REPO, API_LOG).replace(/\\/g, "/"),
    },
    objects: { tenant_id: tenantId, property_id: propertyId },
    checks: {},
    requests: {},
    screenshots: [],
    boundaries: {
      seed_login_used: false,
      dev_auth_used: false,
      development_sms_test_inbox_used: true,
      page_state_faked: false,
      direct_database_business_write: false,
      config_write_sent: false,
      picker_boundary: "supported automator change event; not native system picker overlay",
      real_model_claimed: false,
      production_claimed: false,
    },
  };

  const miniProgram = await automator.connect({ wsEndpoint: WS });
  activeMiniProgram = miniProgram;

  const manager = await smsLogin(miniProgram, "manager");
  if (!manager.data.canViewStats) throw new Error("Tenant Admin home did not expose stats entry");
  if (!(await hasButtonAttribute(manager.page, "data-url", "/pages/supervisor/dashboard"))) {
    throw new Error("Tenant Admin home did not render the stats navigation button");
  }
  await scroll(miniProgram, 9999);
  await screenshot(miniProgram, "01-tenant-admin-home-entry.png");
  evidence.screenshots.push("01-tenant-admin-home-entry.png");

  const managerPropertyOffset = logOffset();
  let page = await tapForPath(
    miniProgram,
    await buttonByAttribute(manager.page, "data-url", "/pages/supervisor/dashboard"),
    "pages/supervisor/dashboard"
  );
  let state = await waitData(
    page,
    (value) => !value.loading && value.overview && !value.error,
    "Tenant Admin property overview"
  );
  if (
    !state.scopeOptions.some((item) => item.value === "TENANT") ||
    !state.scopeOptions.some((item) => item.value === "PROPERTY")
  ) {
    throw new Error("Tenant Admin page did not expose both authorized overview scopes");
  }
  assertSnapshot(state.overview, expected, "PROPERTY", tenantId, propertyId);
  if (
    !(state.incidentStatuses || []).some((item) => item.code === "OPEN" && item.label === "开放") ||
    !(state.slaStates || []).some((item) => item.code === "OPEN" && item.label === "计时中")
  ) {
    throw new Error("Incident or SLA page labels are misleading");
  }
  evidence.requests.tenant_admin_property = assertSuccessfulGets(
    managerPropertyOffset,
    1,
    "Tenant Admin property page"
  );
  await scroll(miniProgram, 0);
  await screenshot(miniProgram, "02-tenant-property-snapshot.png");
  evidence.screenshots.push("02-tenant-property-snapshot.png");

  const tenantScopeIndex = state.scopeOptions.findIndex((item) => item.value === "TENANT");
  const managerTenantOffset = logOffset();
  await tolerateAutomatorTimeout(() =>
    pickerByPrefix(page, "统计口径：").then((item) =>
      item.trigger("change", { value: tenantScopeIndex })
    )
  );
  state = await waitData(
    page,
    (value) =>
      !value.loading &&
      !value.error &&
      value.overview &&
      value.overview.scope_type === "TENANT",
    "Tenant Admin aggregate overview"
  );
  assertSnapshot(state.overview, expected, "TENANT", tenantId, null);
  evidence.requests.tenant_admin_aggregate = assertSuccessfulGets(
    managerTenantOffset,
    1,
    "Tenant Admin aggregate page"
  );
  await scroll(miniProgram, 0);
  await screenshot(miniProgram, "03-tenant-aggregate-snapshot.png");
  evidence.screenshots.push("03-tenant-aggregate-snapshot.png");

  const configPaths = (state.configEntries || []).map((item) => item.path).sort();
  const expectedConfigPaths = [
    "/pages/supervisor/knowledge",
    "/pages/supervisor/properties",
    "/pages/supervisor/sla",
    "/pages/supervisor/staff",
  ].sort();
  assertEqual(configPaths, expectedConfigPaths, "Tenant Admin existing config links");
  await scroll(miniProgram, 9999);
  await screenshot(miniProgram, "04-config-links.png");
  evidence.screenshots.push("04-config-links.png");

  const slaOffset = logOffset();
  page = await tapForPath(
    miniProgram,
    await buttonByAttribute(page, "data-path", "/pages/supervisor/sla"),
    "pages/supervisor/sla"
  );
  const slaState = await waitData(
    page,
    (value) => !value.loading && !value.error && value.effective && value.effective.length === 24,
    "linked SLA page"
  );
  if (Number(slaState.tenantId) !== tenantId || Number(slaState.propertyId) !== propertyId) {
    throw new Error("Existing SLA link did not preserve the selected configuration target");
  }
  const slaGets = requestSummary(slaOffset, "GET", "/api/v1/admin/sla-policies");
  if (!slaGets.length || slaGets.some((item) => item.status !== 200)) {
    throw new Error("Existing SLA link did not read its API data");
  }
  evidence.requests.sla_link = slaGets;
  await screenshot(miniProgram, "05-sla-linked-scope.png");
  evidence.screenshots.push("05-sla-linked-scope.png");

  const propertyAdmin = await smsLogin(miniProgram, "property_admin");
  if (!propertyAdmin.data.canViewStats) {
    throw new Error("Property Admin home did not expose stats entry");
  }
  const propertyOffset = logOffset();
  page = await tapForPath(
    miniProgram,
    await buttonByAttribute(propertyAdmin.page, "data-url", "/pages/supervisor/dashboard"),
    "pages/supervisor/dashboard"
  );
  state = await waitData(
    page,
    (value) => !value.loading && value.overview && !value.error,
    "Property Admin property overview"
  );
  if (
    state.scopeOptions.length !== 1 ||
    state.scopeOptions[0].value !== "PROPERTY" ||
    state.overview.scope_type !== "PROPERTY"
  ) {
    throw new Error("Property Admin page exposed a tenant aggregate scope");
  }
  assertSnapshot(state.overview, expected, "PROPERTY", tenantId, propertyId);
  evidence.requests.property_admin_property = assertSuccessfulGets(
    propertyOffset,
    1,
    "Property Admin page"
  );
  await scroll(miniProgram, 0);
  await screenshot(miniProgram, "06-property-admin-property-only.png");
  evidence.screenshots.push("06-property-admin-property-only.png");

  const resident = await smsLogin(miniProgram, "primary");
  if (
    resident.data.canViewStats ||
    (await hasButtonAttribute(resident.page, "data-url", "/pages/supervisor/dashboard"))
  ) {
    throw new Error("Resident home exposed the stats page");
  }
  await scroll(miniProgram, 9999);
  await screenshot(miniProgram, "07-resident-no-stats-entry.png");
  evidence.screenshots.push("07-resident-no-stats-entry.png");

  const staff = await smsLogin(miniProgram, "staff");
  if (
    staff.data.canViewStats ||
    (await hasButtonAttribute(staff.page, "data-url", "/pages/supervisor/dashboard"))
  ) {
    throw new Error("Staff home exposed the stats page");
  }
  await scroll(miniProgram, 9999);
  await screenshot(miniProgram, "08-staff-no-stats-entry.png");
  evidence.screenshots.push("08-staff-no-stats-entry.png");

  const reloadLogin = await smsLogin(miniProgram, "property_admin");
  const reloadOffset = logOffset();
  await miniProgram.callWxMethod("reLaunch", {
    url: `/pages/supervisor/dashboard?propertyId=${propertyId}&tenantId=${tenantId}`,
  });
  page = await waitPath(miniProgram, "pages/supervisor/dashboard");
  state = await waitData(
    page,
    (value) => !value.loading && value.overview && !value.error,
    "reloaded current property overview"
  );
  assertSnapshot(state.overview, expected, "PROPERTY", tenantId, propertyId);
  evidence.requests.reload = assertSuccessfulGets(reloadOffset, 1, "Reloaded stats page");
  await scroll(miniProgram, 0);
  await screenshot(miniProgram, "09-reload-current-snapshot.png");
  evidence.screenshots.push("09-reload-current-snapshot.png");

  evidence.requests.manager_login = manager.request;
  evidence.requests.property_admin_login = propertyAdmin.request;
  evidence.requests.resident_login = resident.request;
  evidence.requests.staff_login = staff.request;
  evidence.requests.reload_login = reloadLogin.request;
  evidence.checks = {
    tenant_admin_normal_sms_identity: Number(manager.data.me.user_id),
    tenant_admin_home_entry: true,
    tenant_admin_property_snapshot_matches_api_and_database: true,
    tenant_admin_aggregate_snapshot_matches_api_and_database: true,
    current_snapshot_language_and_sla_labels: true,
    existing_config_links_only: expectedConfigPaths,
    config_link_preserved_tenant_and_property: true,
    property_admin_normal_sms_identity: Number(propertyAdmin.data.me.user_id),
    property_admin_has_property_scope_only: true,
    resident_normal_sms_identity: Number(resident.data.me.user_id),
    resident_home_has_no_stats_entry: true,
    staff_normal_sms_identity: Number(staff.data.me.user_id),
    staff_home_has_no_stats_entry: true,
    reload_preserved_same_scope_and_snapshot: true,
  };
  fs.writeFileSync(
    path.join(OUT, "page-evidence.json"),
    `${JSON.stringify(evidence, null, 2)}\n`,
    "utf8"
  );
  console.log(JSON.stringify(evidence, null, 2));
  await miniProgram.disconnect();
  activeMiniProgram = null;
})().catch(async (error) => {
  const safe = redactError(error);
  fs.writeFileSync(path.join(OUT, "page-run-error.txt"), `${safe}\n`, "utf8");
  if (activeMiniProgram) {
    try {
      await activeMiniProgram.disconnect();
    } catch (disconnectError) {
      fs.appendFileSync(
        path.join(OUT, "page-run-error.txt"),
        `${redactError(disconnectError)}\n`,
        "utf8"
      );
    }
  }
  console.error(safe);
  process.exitCode = 1;
});
