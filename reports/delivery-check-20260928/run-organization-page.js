"use strict";

const crypto = require("crypto");
const fs = require("fs");
const path = require("path");
const automator = require(
  "C:/Users/lmj/AppData/Local/Temp/mp-automator-npm/node_modules/miniprogram-automator"
);

const WS = process.env.MP_AUTOMATOR_WS;
const PHONE = process.env.DELIVERY_ADMIN_PHONE;
const PASSWORD = process.env.DELIVERY_ADMIN_PASSWORD;
const INITIAL_ADMIN_PHONE = process.env.DELIVERY_INITIAL_ADMIN_PHONE || PHONE;
const RUN_ID = process.env.DELIVERY_ORG_RUN_ID;
const OUT = __dirname;
const requests = [];

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function tolerateTimeout(operation) {
  try {
    return await operation();
  } catch (error) {
    if (!String(error && error.message).includes("timeout waiting for automator response")) {
      throw error;
    }
    await sleep(600);
    return null;
  }
}

async function waitPage(miniProgram, expected) {
  for (let attempt = 0; attempt < 100; attempt += 1) {
    const page = await miniProgram.currentPage();
    if (page && page.path === expected) return page;
    await sleep(250);
  }
  const page = await miniProgram.currentPage();
  throw new Error(`expected ${expected}; current=${page && page.path}`);
}

async function waitData(page, predicate, label) {
  for (let attempt = 0; attempt < 120; attempt += 1) {
    const data = await page.data();
    if (predicate(data)) return data;
    await sleep(250);
  }
  throw new Error(`${label} timed out`);
}

async function byText(page, selector, text) {
  for (const item of await page.$$(selector)) {
    if (String((await item.text()) || "").includes(text)) return item;
  }
  throw new Error(`${selector} text not found: ${text}`);
}

async function byAttribute(page, selector, name, value) {
  for (const item of await page.$$(selector)) {
    if ((await item.attribute(name)) === value) return item;
  }
  throw new Error(`${selector} ${name} not found: ${value}`);
}

async function fill(page, selector, field, value) {
  const item = await byAttribute(page, selector, "data-field", field);
  await item.input(String(value));
}

async function attachRequestLogger(miniProgram) {
  await miniProgram.exposeFunction("recordOrganizationRequest", (info) => {
    if (!info || !info.url) return;
    const parsed = new URL(info.url);
    requests.push({
      method: info.method || "GET",
      origin: parsed.origin,
      path: parsed.pathname,
      status: info.status || null,
      error: info.error || null,
    });
  });
  await miniProgram.mockWxMethod("request", function recordRequest(options) {
    const self = this;
    return new Promise((resolve, reject) => {
      self.origin({
        ...options,
        success(response) {
          if (typeof recordOrganizationRequest === "function") {
            recordOrganizationRequest({
              url: options.url,
              method: options.method,
              status: response.statusCode,
            });
          }
          if (options.success) options.success(response);
          resolve(response);
        },
        fail(error) {
          if (typeof recordOrganizationRequest === "function") {
            recordOrganizationRequest({
              url: options.url,
              method: options.method,
              error: String(error && error.errMsg ? error.errMsg : error),
            });
          }
          if (options.fail) options.fail(error);
          reject(error);
        },
      });
    });
  });
}

async function main() {
  if (!WS || !PHONE || !PASSWORD || !INITIAL_ADMIN_PHONE || !RUN_ID) {
    throw new Error("required private runtime inputs are missing");
  }
  const miniProgram = await automator.connect({ wsEndpoint: WS });
  try {
    await attachRequestLogger(miniProgram);
    await miniProgram.callWxMethod("clearStorageSync");
    await miniProgram.callWxMethod("reLaunch", { url: "/pages/login/login" });
    let page = await waitPage(miniProgram, "pages/login/login");
    for (const input of await page.$$("input")) {
      const placeholder = await input.attribute("placeholder");
      if (placeholder === "手机号") await input.input(PHONE);
      if (placeholder === "密码") await input.input(PASSWORD);
    }
    await tolerateTimeout(() => byText(page, "button", "登录").then((item) => item.tap()));
    page = await waitPage(miniProgram, "pages/home/home");
    const home = await waitData(
      page,
      (data) => data.me && !data.loading,
      "system administrator home"
    );
    if (!(home.me.roles || []).includes("SYSTEM_ADMIN")) {
      throw new Error("authenticated page user is not SYSTEM_ADMIN");
    }
    const button = await byAttribute(
      page,
      "button",
      "data-url",
      "/pages/system/organizations"
    );
    await tolerateTimeout(() => button.tap());
    page = await waitPage(miniProgram, "pages/system/organizations");
    await fill(page, "input", "tenantName", `交付验收物业-${RUN_ID}`);
    await fill(page, "input", "tenantContact", "021-55550000");
    await fill(page, "input", "propertyName", `交付验收小区-${RUN_ID}`);
    await fill(page, "textarea", "propertyAddress", "上海市合成测试路 28 号");
    await fill(page, "input", "timezone", "Asia/Shanghai");
    await fill(page, "input", "initialAdminPhone", INITIAL_ADMIN_PHONE);
    await tolerateTimeout(() => byText(page, "button", "建立并授权").then((item) => item.tap()));
    const result = await waitData(
      page,
      (data) => data.created && !data.loading,
      "organization creation"
    );
    const screenshot = path.join(OUT, `${RUN_ID}-organization-created.png`);
    await tolerateTimeout(() => miniProgram.screenshot({ path: screenshot }));
    const evidence = {
      task_id: "DELIVERY-ORGANIZATION-ONBOARDING-028",
      run_id: RUN_ID,
      received_at_utc: new Date().toISOString(),
      interaction: "WeChat DevTools automator through actual page controls",
      api_base: "http://127.0.0.1:8093/api/v1",
      login: {
        method: "password",
        role: "SYSTEM_ADMIN",
        user_id: home.me.user_id,
        phone_sha256_16: crypto.createHash("sha256").update(PHONE).digest("hex").slice(0, 16),
      },
      submitted_fields: {
        tenant_name: `交付验收物业-${RUN_ID}`,
        tenant_contact: "021-55550000",
        property_name: `交付验收小区-${RUN_ID}`,
        property_address: "上海市合成测试路 28 号",
        timezone: "Asia/Shanghai",
        initial_admin_phone_redacted: true,
        initial_admin_is_actor: INITIAL_ADMIN_PHONE === PHONE,
      },
      result: result.created,
      notice: result.notice,
      screenshot: path.basename(screenshot),
      boundaries: {
        seed_login: false,
        dev_auth: false,
        business_sql_write: false,
        page_state_faked: false,
        model_requests: 0,
      },
      requests,
    };
    fs.writeFileSync(
      path.join(OUT, `${RUN_ID}-organization-page.json`),
      JSON.stringify(evidence, null, 2),
      "utf8"
    );
    process.stdout.write(
      JSON.stringify({
        run_id: RUN_ID,
        tenant_id: result.created.tenant_id,
        property_id: result.created.property_id,
        initial_admin_user_id: result.created.initial_admin_user_id,
      })
    );
  } finally {
    await miniProgram.disconnect();
  }
}

main().catch((error) => {
  fs.writeFileSync(
    path.join(OUT, `${RUN_ID || "unknown"}-organization-page-error.txt`),
    `${String(error && error.stack ? error.stack : error)}\n${JSON.stringify({ requests }, null, 2)}`,
    "utf8"
  );
  process.exitCode = 1;
});
