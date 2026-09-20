"use strict";

const fs = require("fs");
const http = require("http");
const path = require("path");
const automator = require(process.env.MP_AUTOMATOR_MODULE);

const OUT = path.resolve(__dirname);
const WS = process.env.MP_AUTOMATOR_WS || "ws://127.0.0.1:9435";
const CODE_SHA = process.env.CODE_SHA;
const ACCOUNTS = JSON.parse(
  fs.readFileSync(path.resolve(__dirname, "../mp-current-full-regression/accounts.local.json"), "utf8")
);
const RUN_ID = `ann007-scope-${Date.now().toString(36)}`;

function wait(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function waitUntil(check, label, timeoutMs) {
  const started = Date.now();
  while (Date.now() - started < (timeoutMs || 20000)) {
    const value = await check();
    if (value) return value;
    await wait(250);
  }
  throw new Error(`timeout waiting for ${label}`);
}

function requestJson(method, pathname, body) {
  return new Promise((resolve, reject) => {
    const data = JSON.stringify(body || {});
    const request = http.request(
      {
        host: "127.0.0.1",
        port: 8083,
        path: `/api/v1${pathname}`,
        method,
        headers: {
          "Content-Type": "application/json",
          "Content-Length": Buffer.byteLength(data),
          "X-Request-Id": `${RUN_ID}-inbox`,
        },
      },
      (response) => {
        let raw = "";
        response.on("data", (chunk) => {
          raw += chunk;
        });
        response.on("end", () => {
          let json = {};
          try {
            json = raw ? JSON.parse(raw) : {};
          } catch (error) {
            json = {};
          }
          resolve({ status: response.statusCode, json });
        });
      }
    );
    request.on("error", reject);
    request.write(data);
    request.end();
  });
}

function answer(prompt) {
  const match = String(prompt || "").match(/^(\d+)\s*\+\s*(\d+)\s*=\s*\?$/);
  if (!match) throw new Error("unsupported challenge prompt");
  return String(Number(match[1]) + Number(match[2]));
}

async function current(miniProgram) {
  return miniProgram.currentPage();
}

async function waitPath(miniProgram, fragment) {
  return waitUntil(async () => {
    const page = await current(miniProgram);
    return page && page.path && page.path.indexOf(fragment) >= 0 ? page : null;
  }, fragment);
}

async function tapButton(page, label) {
  const buttons = await page.$$("button");
  for (const button of buttons) {
    if (((await button.text()) || "").trim() === label) {
      await button.tap();
      return;
    }
  }
  throw new Error(`button not found: ${label}`);
}

async function tapContains(page, selector, expected) {
  const nodes = await page.$$(selector);
  for (const node of nodes) {
    const text = ((await node.text()) || "").replace(/\s+/g, " ").trim();
    if (text.indexOf(expected) >= 0) {
      await node.tap();
      return;
    }
  }
  throw new Error(`text not found: ${expected}`);
}

async function fill(page, placeholder, value) {
  const nodes = (await page.$$("input")).concat(await page.$$("textarea"));
  for (const node of nodes) {
    if ((await node.attribute("placeholder")) === placeholder) {
      await node.tap();
      await node.input(String(value));
      return;
    }
  }
  throw new Error(`field not found: ${placeholder}`);
}

async function developmentCode(phone) {
  return waitUntil(async () => {
    const response = await requestJson("POST", "/auth/sms/test-inbox", {
      phone,
      purpose: "login",
    });
    return response.json && response.json.data && response.json.data.code;
  }, "development SMS inbox", 10000);
}

async function smsLogin(miniProgram, phone) {
  await miniProgram.evaluate(() => {
    wx.removeStorageSync("access_token");
    wx.removeStorageSync("refresh_token");
  });
  await miniProgram.reLaunch("/pages/login/login");
  let page = await waitPath(miniProgram, "pages/login/login");
  if ((await page.data()).mode !== "sms") {
    await tapContains(page, ".link-text", "验证码登录");
    await wait(300);
    page = await current(miniProgram);
  }
  await fill(page, "手机号", phone);
  await tapButton(page, "获取验证码");
  await waitUntil(async () => {
    const candidate = await current(miniProgram);
    return (await candidate.data()).challengePrompt ? candidate : null;
  }, "challenge prompt");
  page = await current(miniProgram);
  await fill(page, "挑战答案", answer((await page.data()).challengePrompt));
  await tapButton(page, "获取验证码");
  await waitUntil(async () => {
    const candidate = await current(miniProgram);
    const state = await candidate.data();
    return !state.challengePrompt && Number(state.cooldown) > 0 ? candidate : null;
  }, "SMS delivery");
  page = await current(miniProgram);
  await fill(page, "短信验证码", await developmentCode(phone));
  await tapButton(page, "登录");
  const home = await waitPath(miniProgram, "pages/home/home");
  return home.data();
}

async function openDirect(miniProgram, propertyId, tenantId) {
  const tenantQuery = tenantId === undefined ? "" : `&tenantId=${tenantId}`;
  await miniProgram.reLaunch(
    `/pages/supervisor/announcements?propertyId=${propertyId}${tenantQuery}`
  );
  return waitUntil(async () => {
    const page = await current(miniProgram);
    if (!page || page.path !== "pages/supervisor/announcements") return null;
    const state = await page.data();
    return !state.loading && state.tenantId ? page : null;
  }, "direct announcement route");
}

(async () => {
  if (!CODE_SHA) throw new Error("CODE_SHA is required");
  const miniProgram = await automator.connect({ wsEndpoint: WS });
  try {
    const tenantHome = await smsLogin(miniProgram, ACCOUNTS.phones.tenant_admin);
    const propertyId = Number(tenantHome.propertyId);
    const verifiedTenantId = Number(tenantHome.tenantId);
    let page = await openDirect(miniProgram, propertyId);
    let state = await page.data();
    if (
      Number(state.tenantId) !== verifiedTenantId ||
      !state.canDraft ||
      !state.canPublish ||
      !state.canTenant
    ) {
      throw new Error("propertyId-only tenant route did not derive authorized capabilities");
    }
    const tenantItem = state.items.find((item) => item.scope.scope_type === "TENANT");
    if (!tenantItem) throw new Error("tenant-target announcement is unavailable");
    await tapContains(page, ".card", `#${tenantItem.id} `);
    page = await waitUntil(async () => {
      const candidate = await current(miniProgram);
      const data = await candidate.data();
      return data.selected && Number(data.selected.id) === Number(tenantItem.id)
        ? candidate
        : null;
    }, "tenant-target detail");
    state = await page.data();
    if (state.selected.scope.scope_type !== "TENANT" || !state.canPublish) {
      throw new Error("selected tenant target did not use tenant publish capability");
    }
    await miniProgram.callWxMethod("pageScrollTo", { scrollTop: 9999, duration: 0 });
    await wait(400);
    await miniProgram.screenshot({ path: path.join(OUT, "04-property-id-only-tenant.png") });

    const propertyHome = await smsLogin(miniProgram, ACCOUNTS.phones.property_admin);
    if (Number(propertyHome.propertyId) !== propertyId) {
      throw new Error("configured property administrator does not cover the target property");
    }
    page = await openDirect(miniProgram, propertyId, verifiedTenantId + 999999);
    state = await page.data();
    if (
      Number(state.tenantId) !== verifiedTenantId ||
      !state.canDraft ||
      !state.canPublish ||
      state.canTenant ||
      state.items.some((item) => Number(item.id) === Number(tenantItem.id))
    ) {
      throw new Error("mismatched route tenant affected verified property capabilities");
    }
    await miniProgram.callWxMethod("pageScrollTo", { scrollTop: 0, duration: 0 });
    await wait(400);
    await miniProgram.screenshot({ path: path.join(OUT, "05-mismatched-route-property.png") });

    const evidence = {
      task_id: "ANNOUNCEMENT-GOVERNANCE-007-FIX-2",
      run_id: RUN_ID,
      code_sha: CODE_SHA,
      automator_ws: WS,
      api: "http://127.0.0.1:8083/api/v1",
      auth: "official SMS page controls with DEVELOPMENT SMS provider",
      verified_property_id: propertyId,
      verified_tenant_id: verifiedTenantId,
      checks: {
        property_id_only_tenant_admin_capabilities: true,
        selected_tenant_target_uses_tenant_grant: true,
        mismatched_route_tenant_ignored: true,
        property_admin_tenant_target_hidden: true,
        no_business_state_created: true,
      },
      screenshots: [
        "04-property-id-only-tenant.png",
        "05-mismatched-route-property.png",
      ],
    };
    fs.writeFileSync(
      path.join(OUT, "page-scope-fix-evidence.json"),
      `${JSON.stringify(evidence, null, 2)}\n`,
      "utf8"
    );
    process.stdout.write(`${JSON.stringify(evidence, null, 2)}\n`);
  } finally {
    await miniProgram.disconnect();
  }
})().catch((error) => {
  process.stderr.write(`${error.stack || error.message}\n`);
  process.exitCode = 1;
});
