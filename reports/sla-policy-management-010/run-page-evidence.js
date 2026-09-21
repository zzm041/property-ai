"use strict";

const crypto = require("crypto");
const fs = require("fs");
const http = require("http");
const path = require("path");
const automator = require(process.env.MP_AUTOMATOR_MODULE);

const REPO = path.resolve(__dirname, "..", "..");
const OUT = __dirname;
const PRIVATE_RUN = path.join(REPO, "tmp", "sla-policy-management-010", "context.json");
const API_LOG = process.env.MP_API_LOG;
const WS = process.env.MP_AUTOMATOR_WS;
const RESUME_MANAGER = process.env.MP_RESUME_MANAGER === "1";
const FINAL_VISUAL_ONLY = process.env.MP_FINAL_VISUAL_ONLY === "1";
const CODE_SHA = "7814d45531a91efb6a63c63df261415fa446a1c4";
const CATEGORY = "OTHER";
const PRIORITY = "LOW";
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
    for (const session of Object.values(privateRun.sessions || {})) {
      if (!session || typeof session !== "object") continue;
      for (const key of ["access_token", "refresh_token"]) {
        if (session[key]) text = text.split(String(session[key])).join("[REDACTED_TOKEN]");
      }
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

async function inputByField(page, field) {
  for (const element of await page.$$("input")) {
    if ((await element.attribute("data-field")) === field) return element;
  }
  throw new Error(`Input field not found: ${field}`);
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
  await tolerateAutomatorTimeout(() => buttonByText(page, "获取验证码", true).then((item) => item.tap()));
  state = await waitData(
    page,
    (value) => Boolean(value.challengePrompt) && !value.loading,
    `${role} challenge`
  );
  await fillPlaceholder(page, "挑战答案", answer(state.challengePrompt));
  await tolerateAutomatorTimeout(() => buttonByText(page, "获取验证码", true).then((item) => item.tap()));
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

async function scroll(miniProgram, top) {
  await tolerateAutomatorTimeout(() =>
    miniProgram.callWxMethod("pageScrollTo", { scrollTop: top, duration: 0 })
  );
  await wait(400);
}

function findPolicy(items, propertyId) {
  return (items || []).find(
    (item) =>
      item.category === CATEGORY &&
      item.priority === PRIORITY &&
      (propertyId === null
        ? item.property_id === null
        : Number(item.property_id) === Number(propertyId))
  );
}

function findEffective(items) {
  return (items || []).find(
    (item) => item.category === CATEGORY && item.priority === PRIORITY
  );
}

async function choosePolicyKey(page) {
  let state = await page.data();
  const categoryIndex = state.categoryOptions.findIndex((item) => item.value === CATEGORY);
  const priorityIndex = state.priorityOptions.findIndex((item) => item.value === PRIORITY);
  if (categoryIndex < 0 || priorityIndex < 0) throw new Error("Dedicated page policy key is missing");
  await tolerateAutomatorTimeout(() =>
    pickerByPrefix(page, "工单类型：").then((item) =>
      item.trigger("change", { value: categoryIndex })
    )
  );
  state = await waitData(page, (value) => Number(value.categoryIndex) === categoryIndex, "category picker");
  await tolerateAutomatorTimeout(() =>
    pickerByPrefix(page, "优先级：").then((item) =>
      item.trigger("change", { value: priorityIndex })
    )
  );
  return waitData(page, (value) => Number(value.priorityIndex) === priorityIndex, "priority picker");
}

async function setMinutes(page, respond, complete) {
  await (await inputByField(page, "respondMinutes")).input(String(respond));
  await (await inputByField(page, "completeMinutes")).input(String(complete));
  return waitData(
    page,
    (value) =>
      String(value.respondMinutes) === String(respond) &&
      String(value.completeMinutes) === String(complete),
    "SLA minute inputs"
  );
}

function assertSaveRequests(offset, label) {
  const puts = requestSummary(offset, "PUT", "/api/v1/admin/sla-policies");
  const gets = requestSummary(offset, "GET", "/api/v1/admin/sla-policies");
  if (puts.length !== 1 || puts[0].status !== 200) {
    throw new Error(`${label} did not produce exactly one successful SLA PUT`);
  }
  if (!gets.length || gets.some((item) => item.status !== 200)) {
    throw new Error(`${label} did not reload SLA data successfully`);
  }
  return { put: puts[0], get: gets };
}

(async () => {
  if (!WS) throw new Error("MP_AUTOMATOR_WS is required");
  if (!API_LOG) throw new Error("MP_API_LOG is required");
  privateRun = JSON.parse(fs.readFileSync(PRIVATE_RUN, "utf8"));
  if (privateRun.stage !== "complete") throw new Error("API private context is incomplete");
  const propertyId = Number(privateRun.org.property_id);
  const tenantId = Number(privateRun.org.tenant_id);
  const evidence = {
    task_id: "SLA-POLICY-MGMT-010",
    phase: "wechat_devtools_page",
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
      picker_boundary: "supported automator change event; not native system picker overlay",
      real_model_claimed: false,
      production_claimed: false,
    },
  };

  const miniProgram = await automator.connect({ wsEndpoint: WS });
  activeMiniProgram = miniProgram;

  if (FINAL_VISUAL_ONLY) {
    const priorPageEvidence = JSON.parse(
      fs.readFileSync(path.join(OUT, "page-evidence.json"), "utf8")
    );
    const propertyAdmin = await smsLogin(miniProgram, "property_admin");
    if (!propertyAdmin.data.canManageSla) {
      throw new Error("Property Admin home did not expose SLA entry for final visual check");
    }
    let finalPage = await tapForPath(
      miniProgram,
      await buttonByDataUrl(propertyAdmin.page, "/pages/supervisor/sla"),
      "pages/supervisor/sla"
    );
    await waitData(
      finalPage,
      (value) => !value.loading && value.effective.length === 24 && !value.error,
      "final visual SLA page"
    );
    const finalState = await choosePolicyKey(finalPage);
    const finalPolicy = findPolicy(finalState.items, propertyId);
    const finalEffective = findEffective(finalState.effective);
    if (
      !finalPolicy ||
      finalPolicy.status !== "DISABLED" ||
      !finalEffective ||
      finalEffective.source !== "TENANT" ||
      Number(finalEffective.policy_id) !==
        Number(priorPageEvidence.objects.tenant_policy_id)
    ) {
      throw new Error("Final visual state did not show the persisted disabled fallback");
    }
    await wait(5000);
    await miniProgram.screenshot({ path: path.join(OUT, "09-disabled-editor-after-relogin.png") });
    await miniProgram.evaluate(() => {
      wx.pageScrollTo({ scrollTop: 99999, duration: 0 });
      return true;
    });
    await wait(5000);
    await miniProgram.screenshot({ path: path.join(OUT, "10-effective-and-saved-layers.png") });
    const supplemental = {
      task_id: "SLA-POLICY-MGMT-010",
      phase: "wechat_devtools_final_visual_read_only",
      captured_at: new Date().toISOString(),
      code_sha: CODE_SHA,
      run_id: privateRun.run_id,
      objects: {
        tenant_id: tenantId,
        property_id: propertyId,
        tenant_policy_id: Number(finalEffective.policy_id),
        property_policy_id: Number(finalPolicy.id),
      },
      checks: {
        normal_property_admin_sms_login: true,
        selected_category: CATEGORY,
        selected_priority: PRIORITY,
        persisted_property_status: finalPolicy.status,
        effective_source: finalEffective.source,
        effective_respond_minutes: Number(finalEffective.respond_minutes),
        effective_complete_minutes: Number(finalEffective.complete_minutes),
      },
      screenshots: [
        "09-disabled-editor-after-relogin.png",
        "10-effective-and-saved-layers.png",
      ],
      boundaries: {
        policy_write_sent: false,
        page_state_faked: false,
        seed_login_used: false,
        development_sms_test_inbox_used: true,
        production_claimed: false,
      },
    };
    fs.writeFileSync(
      path.join(OUT, "final-visual-evidence.json"),
      `${JSON.stringify(supplemental, null, 2)}\n`,
      "utf8"
    );
    console.log(JSON.stringify(supplemental, null, 2));
    await miniProgram.disconnect();
    activeMiniProgram = null;
    return;
  }

  let manager;
  if (RESUME_MANAGER) {
    await tolerateAutomatorTimeout(() =>
      miniProgram.callWxMethod("reLaunch", { url: "/pages/home/home" })
    );
    const managerPage = await waitPath(miniProgram, "pages/home/home");
    const managerData = await waitData(
      managerPage,
      (value) => Boolean(value.me && value.me.user_id) && !value.loading,
      "resumed manager home identity"
    );
    if (Number(managerData.me.user_id) !== Number(privateRun.sessions.manager.user_id)) {
      throw new Error("Resumed page session is not the expected synthetic Tenant Admin");
    }
    manager = {
      page: managerPage,
      data: managerData,
      request: { status: 200, resumed_existing_normal_sms_session: true },
    };
    evidence.boundaries.manager_session_resume =
      "same-task page SMS session from the prior screenshot-timeout attempt";
  } else {
    manager = await smsLogin(miniProgram, "manager");
  }
  if (!manager.data.canManageSla) throw new Error("Tenant Admin home did not expose SLA entry");
  await scroll(miniProgram, 9999);
  await miniProgram.screenshot({ path: path.join(OUT, "01-tenant-admin-home-entry.png") });
  evidence.screenshots.push("01-tenant-admin-home-entry.png");
  const managerOpenOffset = logOffset();
  let page = await tapForPath(
    miniProgram,
    await buttonByDataUrl(manager.page, "/pages/supervisor/sla"),
    "pages/supervisor/sla"
  );
  let state = await waitData(
    page,
    (value) => !value.loading && value.propertyId && value.effective.length === 24 && !value.error,
    "Tenant Admin SLA page"
  );
  if (
    Number(state.propertyId) !== propertyId ||
    Number(state.tenantId) !== tenantId ||
    !state.canTenant ||
    !state.scopeOptions.some((item) => item.value === "TENANT")
  ) {
    throw new Error("Tenant Admin SLA scope did not match the authenticated fixture");
  }
  const managerOpenRequests = requestSummary(
    managerOpenOffset,
    "GET",
    "/api/v1/admin/sla-policies"
  );
  if (!managerOpenRequests.length || managerOpenRequests.some((item) => item.status !== 200)) {
    throw new Error("Tenant Admin page did not load SLA policies from the API");
  }
  await wait(5000);
  await miniProgram.screenshot({ path: path.join(OUT, "02-tenant-default-editor.png") });
  evidence.screenshots.push("02-tenant-default-editor.png");

  const tenantScopeIndex = state.scopeOptions.findIndex((item) => item.value === "TENANT");
  await tolerateAutomatorTimeout(() =>
    pickerByPrefix(page, "作用范围：").then((item) =>
      item.trigger("change", { value: tenantScopeIndex })
    )
  );
  await waitData(page, (value) => Number(value.scopeIndex) === tenantScopeIndex, "tenant scope picker");
  state = await choosePolicyKey(page);
  if (findPolicy(state.items, null)) throw new Error("Dedicated tenant page policy already existed");
  await setMinutes(page, 19, 49);
  const tenantSaveOffset = logOffset();
  await tolerateAutomatorTimeout(() =>
    buttonByText(page, "保存并重新读取", true).then((item) => item.tap())
  );
  state = await waitData(
    page,
    (value) => {
      const policy = findPolicy(value.items, null);
      const effective = findEffective(value.effective);
      return (
        !value.loading &&
        !value.error &&
        String(value.notice || "").includes("已保存") &&
        policy &&
        policy.status === "ACTIVE" &&
        Number(policy.respond_minutes) === 19 &&
        Number(policy.complete_minutes) === 49 &&
        effective &&
        effective.source === "TENANT"
      );
    },
    "tenant default save"
  );
  const tenantPolicy = findPolicy(state.items, null);
  evidence.requests.tenant_save = assertSaveRequests(tenantSaveOffset, "tenant page save");
  await wait(5000);
  await miniProgram.screenshot({ path: path.join(OUT, "03-tenant-default-saved.png") });
  evidence.screenshots.push("03-tenant-default-saved.png");

  const propertyAdmin = await smsLogin(miniProgram, "property_admin");
  if (!propertyAdmin.data.canManageSla) {
    throw new Error("Property Admin home did not expose SLA entry");
  }
  const propertyOpenOffset = logOffset();
  page = await tapForPath(
    miniProgram,
    await buttonByDataUrl(propertyAdmin.page, "/pages/supervisor/sla"),
    "pages/supervisor/sla"
  );
  state = await waitData(
    page,
    (value) => !value.loading && value.effective.length === 24 && !value.error,
    "Property Admin SLA page"
  );
  if (
    state.canTenant ||
    state.scopeOptions.length !== 1 ||
    state.scopeOptions[0].value !== "PROPERTY" ||
    Number(state.propertyId) !== propertyId
  ) {
    throw new Error("Property Admin page exposed a tenant-default editor");
  }
  const propertyOpenRequests = requestSummary(
    propertyOpenOffset,
    "GET",
    "/api/v1/admin/sla-policies"
  );
  if (!propertyOpenRequests.length || propertyOpenRequests.some((item) => item.status !== 200)) {
    throw new Error("Property Admin page did not load SLA policies from the API");
  }
  await wait(5000);
  await miniProgram.screenshot({ path: path.join(OUT, "04-property-admin-property-only.png") });
  evidence.screenshots.push("04-property-admin-property-only.png");
  state = await choosePolicyKey(page);
  if (findPolicy(state.items, propertyId)) throw new Error("Dedicated property page policy already existed");
  const inherited = findEffective(state.effective);
  if (
    !inherited ||
    inherited.source !== "TENANT" ||
    Number(inherited.policy_id) !== Number(tenantPolicy.id)
  ) {
    throw new Error("Property editor did not inherit the saved tenant default");
  }
  await setMinutes(page, 12, 42);
  const propertySaveOffset = logOffset();
  await tolerateAutomatorTimeout(() =>
    buttonByText(page, "保存并重新读取", true).then((item) => item.tap())
  );
  state = await waitData(
    page,
    (value) => {
      const policy = findPolicy(value.items, propertyId);
      const effective = findEffective(value.effective);
      return (
        !value.loading &&
        !value.error &&
        policy &&
        policy.status === "ACTIVE" &&
        Number(policy.respond_minutes) === 12 &&
        Number(policy.complete_minutes) === 42 &&
        effective &&
        effective.source === "PROPERTY" &&
        Number(effective.policy_id) === Number(policy.id)
      );
    },
    "property override save"
  );
  const propertyPolicy = findPolicy(state.items, propertyId);
  evidence.requests.property_save = assertSaveRequests(propertySaveOffset, "property page save");
  await wait(5000);
  await miniProgram.screenshot({ path: path.join(OUT, "05-property-override-active.png") });
  evidence.screenshots.push("05-property-override-active.png");

  await tolerateAutomatorTimeout(() =>
    pickerByPrefix(page, "状态：").then((item) => item.trigger("change", { value: 1 }))
  );
  await waitData(page, (value) => Number(value.statusIndex) === 1, "property disable picker");
  const disableOffset = logOffset();
  await tolerateAutomatorTimeout(() =>
    buttonByText(page, "保存并重新读取", true).then((item) => item.tap())
  );
  state = await waitData(
    page,
    (value) => {
      const policy = findPolicy(value.items, propertyId);
      const effective = findEffective(value.effective);
      return (
        !value.loading &&
        !value.error &&
        policy &&
        policy.status === "DISABLED" &&
        Number(policy.id) === Number(propertyPolicy.id) &&
        effective &&
        effective.source === "TENANT" &&
        Number(effective.policy_id) === Number(tenantPolicy.id) &&
        Number(effective.respond_minutes) === 19 &&
        Number(effective.complete_minutes) === 49
      );
    },
    "disabled property fallback"
  );
  evidence.requests.property_disable = assertSaveRequests(disableOffset, "property disable save");
  await wait(5000);
  await miniProgram.screenshot({
    path: path.join(OUT, "06-property-disabled-tenant-fallback.png"),
  });
  evidence.screenshots.push("06-property-disabled-tenant-fallback.png");

  const reloadOffset = logOffset();
  await miniProgram.callWxMethod("reLaunch", {
    url: `/pages/supervisor/sla?propertyId=${propertyId}&tenantId=${tenantId}`,
  });
  page = await waitPath(miniProgram, "pages/supervisor/sla");
  state = await waitData(
    page,
    (value) => {
      const policy = findPolicy(value.items, propertyId);
      const effective = findEffective(value.effective);
      return (
        !value.loading &&
        policy &&
        policy.status === "DISABLED" &&
        effective &&
        effective.source === "TENANT"
      );
    },
    "persisted disabled fallback after page reload"
  );
  const reloadRequests = requestSummary(reloadOffset, "GET", "/api/v1/admin/sla-policies");
  if (!reloadRequests.length || reloadRequests.some((item) => item.status !== 200)) {
    throw new Error("Reloaded SLA page did not read persisted server state");
  }
  await wait(5000);
  await miniProgram.screenshot({ path: path.join(OUT, "07-reload-persisted-fallback.png") });
  evidence.screenshots.push("07-reload-persisted-fallback.png");

  const staff = await smsLogin(miniProgram, "staff");
  if (staff.data.canManageSla) throw new Error("Staff home unexpectedly exposed SLA management");
  let slaEntryFound = false;
  for (const button of await staff.page.$$("button")) {
    if ((await button.attribute("data-url")) === "/pages/supervisor/sla") {
      slaEntryFound = true;
    }
  }
  if (slaEntryFound) throw new Error("Staff home rendered the SLA navigation button");
  await scroll(miniProgram, 9999);
  await miniProgram.screenshot({ path: path.join(OUT, "08-staff-no-sla-entry.png") });
  evidence.screenshots.push("08-staff-no-sla-entry.png");

  evidence.objects.tenant_policy_id = Number(tenantPolicy.id);
  evidence.objects.property_policy_id = Number(propertyPolicy.id);
  evidence.checks = {
    tenant_admin_normal_sms_identity: Number(manager.data.me.user_id),
    tenant_admin_home_entry: true,
    tenant_admin_scope_and_effective_matrix: true,
    tenant_default_saved_via_page: {
      category: CATEGORY,
      priority: PRIORITY,
      respond_minutes: 19,
      complete_minutes: 49,
      policy_id: Number(tenantPolicy.id),
    },
    property_admin_normal_sms_identity: Number(propertyAdmin.data.me.user_id),
    property_admin_has_no_tenant_editor: true,
    property_override_saved_via_page: {
      respond_minutes: 12,
      complete_minutes: 42,
      policy_id: Number(propertyPolicy.id),
    },
    disabled_property_override_persisted: true,
    effective_fallback_after_disable: {
      source: "TENANT",
      policy_id: Number(tenantPolicy.id),
      respond_minutes: 19,
      complete_minutes: 49,
    },
    reload_preserved_same_policy_and_fallback: true,
    staff_home_has_no_sla_entry: true,
  };
  evidence.requests.manager_open = managerOpenRequests;
  evidence.requests.property_open = propertyOpenRequests;
  evidence.requests.reload = reloadRequests;
  evidence.requests.manager_login = manager.request;
  evidence.requests.property_admin_login = propertyAdmin.request;
  evidence.requests.staff_login = staff.request;
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
