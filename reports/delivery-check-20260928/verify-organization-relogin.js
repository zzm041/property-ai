"use strict";

const fs = require("fs");
const path = require("path");
const automator = require(
  "C:/Users/lmj/AppData/Local/Temp/mp-automator-npm/node_modules/miniprogram-automator"
);

const WS = process.env.MP_AUTOMATOR_WS;
const PHONE = process.env.DELIVERY_ADMIN_PHONE;
const PASSWORD = process.env.DELIVERY_ADMIN_PASSWORD;
const RUN_ID = process.env.DELIVERY_ORG_RUN_ID;
const EXPECTED_TENANT_ID = Number(process.env.DELIVERY_EXPECTED_TENANT_ID);
const EXPECTED_PROPERTY_ID = Number(process.env.DELIVERY_EXPECTED_PROPERTY_ID);
const OUT = __dirname;

function sleep(ms) { return new Promise((resolve) => setTimeout(resolve, ms)); }

async function tolerateTimeout(operation) {
  try { return await operation(); }
  catch (error) {
    if (!String(error && error.message).includes("timeout waiting for automator response")) throw error;
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
  throw new Error(`expected page ${expected}`);
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

async function byUrl(page, url) {
  for (const item of await page.$$("button")) {
    if ((await item.attribute("data-url")) === url) return item;
  }
  throw new Error(`button URL not found: ${url}`);
}

async function main() {
  if (!WS || !PHONE || !PASSWORD || !RUN_ID || !EXPECTED_TENANT_ID || !EXPECTED_PROPERTY_ID) {
    throw new Error("required private/runtime inputs are missing");
  }
  const miniProgram = await automator.connect({ wsEndpoint: WS });
  try {
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
    const home = await waitData(page, (data) => data.me && !data.loading, "refreshed home");
    const systemGrant = (home.me.scopes || []).find(
      (scope) => scope.role_code === "SYSTEM_ADMIN" && scope.scope_type === "SYSTEM"
    );
    if (systemGrant) throw new Error("independent tenant admin unexpectedly has SYSTEM_ADMIN");
    const tenantGrant = (home.me.scopes || []).find(
      (scope) => scope.role_code === "TENANT_ADMIN" &&
        scope.scope_type === "TENANT" &&
        Number(scope.tenant_id) === EXPECTED_TENANT_ID
    );
    if (!tenantGrant) throw new Error("new TENANT_ADMIN grant missing after re-login");
    if (!home.canViewProperty) throw new Error("property management entry missing after re-login");
    await tolerateTimeout(() => byUrl(page, "/pages/supervisor/properties").then((item) => item.tap()));
    page = await waitPage(miniProgram, "pages/supervisor/properties");
    const archive = await waitData(
      page,
      (data) => !data.loading && (data.properties || []).some(
        (item) => Number(item.id) === EXPECTED_PROPERTY_ID && item.canEdit
      ),
      "authorized property archive"
    );
    const selectedIndex = archive.properties.findIndex(
      (item) => Number(item.id) === EXPECTED_PROPERTY_ID
    );
    const unrelatedProperties = (archive.properties || []).filter(
      (item) => Number(item.id) !== EXPECTED_PROPERTY_ID
    );
    if (unrelatedProperties.length) {
      throw new Error("independent tenant admin can see an unrelated property");
    }
    const picker = await page.$("picker");
    if (!picker || selectedIndex < 0) throw new Error("expected property picker is unavailable");
    await picker.trigger("change", { value: selectedIndex });
    await waitData(
      page,
      (data) => Number(data.propertyId) === EXPECTED_PROPERTY_ID && !data.loading,
      "selected authorized property"
    );
    await sleep(1200);
    const screenshot = path.join(OUT, `${RUN_ID}-relogin-property-scope.png`);
    await tolerateTimeout(() => miniProgram.screenshot({ path: screenshot }));
    const property = archive.properties.find((item) => Number(item.id) === EXPECTED_PROPERTY_ID);
    const evidence = {
      task_id: "DELIVERY-ORGANIZATION-ONBOARDING-028",
      run_id: RUN_ID,
      received_at_utc: new Date().toISOString(),
      interaction: "normal password re-login and actual management page navigation",
      user_id: home.me.user_id,
      system_admin_absent: true,
      role_seen: tenantGrant.role_code,
      scope: {
        scope_type: tenantGrant.scope_type,
        tenant_id: tenantGrant.tenant_id,
        property_id: null,
      },
      management_property: {
        id: property.id,
        tenant_id: property.tenant_id,
        name: property.name,
        can_edit: property.canEdit,
      },
      unrelated_property_ids_visible: [],
      screenshot: path.basename(screenshot),
      boundaries: {
        business_request: false,
        seed_login: false,
        page_state_faked: false,
      },
    };
    fs.writeFileSync(
      path.join(OUT, `${RUN_ID}-organization-relogin.json`),
      JSON.stringify(evidence, null, 2),
      "utf8"
    );
    process.stdout.write(JSON.stringify({
      run_id: RUN_ID,
      user_id: home.me.user_id,
      tenant_admin_scope: EXPECTED_TENANT_ID,
      property_visible: EXPECTED_PROPERTY_ID,
      can_edit: property.canEdit,
      system_admin_absent: true,
      unrelated_property_count: 0,
    }));
  } finally {
    await miniProgram.disconnect();
  }
}

main().catch((error) => {
  fs.writeFileSync(
    path.join(OUT, `${RUN_ID || "unknown"}-organization-relogin-error.txt`),
    String(error && error.stack ? error.stack : error),
    "utf8"
  );
  process.exitCode = 1;
});
