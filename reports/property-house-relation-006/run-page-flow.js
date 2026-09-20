"use strict";

const fs = require("fs");
const http = require("http");
const path = require("path");
const automator = require(process.env.MP_AUTOMATOR_MODULE);

const OUT = path.resolve(__dirname);
const WS = process.env.MP_AUTOMATOR_WS || "ws://127.0.0.1:9435";
const ACCOUNTS = JSON.parse(
  fs.readFileSync(
    path.resolve(__dirname, "../mp-current-full-regression/accounts.local.json"),
    "utf8"
  )
);
const RUN_ID = `property-house-page-${Date.now().toString(36)}`;
const SUFFIX = Date.now().toString(36).slice(-6).toUpperCase();

function wait(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function waitUntil(check, label, timeoutMs) {
  const started = Date.now();
  while (Date.now() - started < (timeoutMs || 15000)) {
    const value = await check();
    if (value) return value;
    await wait(250);
  }
  throw new Error(`timeout waiting for ${label}`);
}

function requestJson(method, pathname, body) {
  return new Promise((resolve, reject) => {
    const data = body === undefined ? "" : JSON.stringify(body);
    const request = http.request(
      {
        host: "127.0.0.1",
        port: 8083,
        path: `/api/v1${pathname}`,
        method,
        headers: {
          "Content-Type": "application/json",
          "X-Request-Id": `${RUN_ID}-inbox`,
          ...(data ? { "Content-Length": Buffer.byteLength(data) } : {}),
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
    if (data) request.write(data);
    request.end();
  });
}

function challengeAnswer(prompt) {
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
  }, fragment, 20000);
}

async function tapButton(page, label) {
  const buttons = await page.$$("button");
  const seen = [];
  for (const button of buttons) {
    const text = ((await button.text()) || "").trim();
    seen.push(text);
    if (text === label) {
      await button.tap();
      return;
    }
  }
  throw new Error(`button not found: ${label}; have ${seen.join("|")}`);
}

async function tapText(page, selector, expected) {
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

async function fill(page, placeholder, value, occurrence) {
  const controls = [...(await page.$$("input")), ...(await page.$$("textarea"))];
  const matching = [];
  for (const control of controls) {
    if ((await control.attribute("placeholder")) === placeholder) matching.push(control);
  }
  if (!matching.length) throw new Error(`input not found: ${placeholder}`);
  const index = occurrence === "last" ? matching.length - 1 : Number(occurrence || 0);
  await matching[index].tap();
  await matching[index].input(String(value));
}

async function fillTextarea(page, value) {
  const textareas = await page.$$("textarea");
  if (textareas.length !== 1) throw new Error(`expected one CSV textarea; have ${textareas.length}`);
  await textareas[0].tap();
  await textareas[0].input(String(value));
}

async function getDevelopmentSms(phone) {
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
  let state = await page.data();
  if (state.mode !== "sms") {
    await tapText(page, ".link-text", "验证码登录");
    await wait(300);
    page = await current(miniProgram);
  }
  await fill(page, "手机号", phone);
  await tapButton(page, "获取验证码");
  await waitUntil(async () => {
    const candidate = await current(miniProgram);
    const data = await candidate.data();
    return data.challengePrompt ? candidate : null;
  }, "login challenge");
  page = await current(miniProgram);
  state = await page.data();
  await fill(page, "挑战答案", challengeAnswer(state.challengePrompt));
  await tapButton(page, "获取验证码");
  await waitUntil(async () => {
    const candidate = await current(miniProgram);
    const data = await candidate.data();
    return !data.challengePrompt && Number(data.cooldown) > 0 ? candidate : null;
  }, "SMS delivery completion");
  const code = await getDevelopmentSms(phone);
  page = await current(miniProgram);
  await fill(page, "短信验证码", code);
  await tapButton(page, "登录");
  return waitPath(miniProgram, "pages/home/home");
}

async function pageStateWhen(miniProgram, predicate, label) {
  return waitUntil(async () => {
    const page = await current(miniProgram);
    const state = await page.data();
    return predicate(state) ? { page, state } : null;
  }, label);
}

async function screenshot(miniProgram, name) {
  await wait(500);
  await miniProgram.screenshot({ path: path.join(OUT, name) });
}

(async () => {
  if (!ACCOUNTS.phones || !ACCOUNTS.phones.property_admin) {
    throw new Error("existing property_admin test identity is unavailable");
  }
  const miniProgram = await automator.connect({ wsEndpoint: WS });
  let page = await smsLogin(miniProgram, ACCOUNTS.phones.property_admin);
  let home = await page.data();
  if (!home.canManageProperty || !home.tenantId || !home.propertyId) {
    throw new Error("property admin lacks scoped property management entry");
  }
  await tapButton(page, "楼栋房屋与住户关系");
  ({ page } = await pageStateWhen(
    miniProgram,
    (state) => state.canEdit && !state.loading && state.properties.length > 0,
    "property archive page"
  ));
  await screenshot(miniProgram, "01-property-page-entry.png");

  const buildingCode = `UIB${SUFFIX}`;
  const unitCode = `UIU${SUFFIX}`;
  const roomNo = `UI-${SUFFIX}`;
  await fill(page, "楼栋编码", buildingCode);
  await fill(page, "楼栋名称", `页面楼栋 ${SUFFIX}`);
  await tapButton(page, "建立楼栋");
  let observed = await pageStateWhen(
    miniProgram,
    (state) => (state.buildings || []).some((item) => item.code === buildingCode),
    "UI-created building"
  );
  page = observed.page;
  let state = observed.state;
  const buildingIndex = state.buildingOptions.findIndex((item) => item.code === buildingCode);
  let pickers = await page.$$("picker");
  await pickers[1].trigger("change", { value: buildingIndex });
  await fill(page, "单元编码", unitCode);
  await fill(page, "单元名称", `页面单元 ${SUFFIX}`);
  await tapButton(page, "建立单元");
  observed = await pageStateWhen(
    miniProgram,
    (candidate) => (candidate.units || []).some((item) => item.code === unitCode),
    "UI-created unit"
  );
  page = observed.page;
  state = observed.state;
  const unitIndex = state.unitOptions.findIndex((item) => item.code === unitCode);
  pickers = await page.$$("picker");
  await pickers[2].trigger("change", { value: unitIndex });
  await fill(page, "房号", roomNo, 0);
  await fill(page, "楼层（可不填）", "6");
  await tapButton(page, "建立房屋档案");
  observed = await pageStateWhen(
    miniProgram,
    (candidate) => (candidate.houses || []).some((item) => item.room_no === roomNo),
    "UI-created house"
  );
  page = observed.page;
  state = observed.state;
  const createdHouse = state.houses.find((item) => item.room_no === roomNo);
  const card = await page.$(`.house-card[data-id="${createdHouse.id}"]`);
  const offset = await card.offset();
  await miniProgram.callWxMethod("pageScrollTo", {
    scrollTop: Math.max(0, Number(offset.top) - 20),
    duration: 0,
  });
  await screenshot(miniProgram, "02-created-house.png");

  pickers = await page.$$("picker");
  await pickers[pickers.length - 1].trigger("change", { value: 1 });
  await tapButton(page, "保存房屋档案");
  observed = await pageStateWhen(
    miniProgram,
    (candidate) =>
      (candidate.houses || []).some(
        (item) => item.id === createdHouse.id && item.status === "INACTIVE"
      ),
    "house inactive update"
  );
  page = observed.page;
  pickers = await page.$$("picker");
  await pickers[pickers.length - 1].trigger("change", { value: 0 });
  await tapButton(page, "保存房屋档案");
  observed = await pageStateWhen(
    miniProgram,
    (candidate) =>
      (candidate.houses || []).some(
        (item) => item.id === createdHouse.id && item.status === "ACTIVE"
      ),
    "house active update"
  );
  page = observed.page;

  const importBuilding = `UIC${SUFFIX}`;
  const importUnit = `UIV${SUFFIX}`;
  const importRoom = `IMP-${SUFFIX}`;
  const csv =
    "building_code,building_name,unit_code,unit_name,room_no,floor,status\n" +
    `${importBuilding},页面导入楼栋 ${SUFFIX},${importUnit},页面导入单元 ${SUFFIX},${importRoom},7,ACTIVE`;
  await fillTextarea(page, csv);
  await tapButton(page, "导入 CSV 内容");
  observed = await pageStateWhen(
    miniProgram,
    (candidate) =>
      (candidate.houses || []).some((item) => item.room_no === importRoom) &&
      String(candidate.notice || "").indexOf("导入完成") >= 0,
    "UI CSV import"
  );
  page = observed.page;
  await tapButton(page, "导出并复制 CSV");
  observed = await pageStateWhen(
    miniProgram,
    (candidate) => String(candidate.notice || "").indexOf("已导出并复制") >= 0,
    "UI CSV export"
  );
  state = observed.state;
  await miniProgram.callWxMethod("pageScrollTo", { scrollTop: 0, duration: 0 });
  await screenshot(miniProgram, "03-import-export-complete.png");

  const relationStatusCounts = (state.relations || []).reduce((counts, relation) => {
    counts[relation.status] = (counts[relation.status] || 0) + 1;
    return counts;
  }, {});
  const evidence = {
    task_id: "PROPERTY-HOUSE-RELATION-006",
    run_id: RUN_ID,
    code_sha_product: process.env.CODE_SHA || "UNKNOWN",
    environment: "WeChat DevTools automator -> 127.0.0.1:8083/api/v1 -> isolated 0009",
    interaction: "actual mini-program page controls with an official SMS session",
    provider_mode: "development SMS substitute; no real provider claim",
    actor: {
      role: "PROPERTY_ADMIN",
      scope_type: "PROPERTY",
      system_actor: false,
      phone_saved: false,
      token_saved: false,
    },
    assertions: {
      home_exposed_scoped_entry: true,
      archive_page_editable: true,
      building_created_through_page: true,
      unit_created_through_page: true,
      house_created_through_page: true,
      house_inactive_active_roundtrip_through_page: true,
      csv_import_through_page: true,
      csv_export_through_page: true,
      relation_history_loaded: (state.relations || []).length > 0,
    },
    created_synthetic_codes: {
      building_code: buildingCode,
      unit_code: unitCode,
      room_no: roomNo,
      imported_building_code: importBuilding,
      imported_unit_code: importUnit,
      imported_room_no: importRoom,
    },
    observed_counts: {
      buildings: state.buildings.length,
      units: state.units.length,
      houses: state.houses.length,
      relations: state.relations.length,
      relation_statuses: relationStatusCounts,
    },
    screenshots: [
      "01-property-page-entry.png",
      "02-created-house.png",
      "03-import-export-complete.png",
    ],
    not_claimed: [
      "real SMS provider",
      "real self-hosted model",
      "production environment",
      "Production V1 acceptance",
    ],
  };
  fs.writeFileSync(
    path.join(OUT, "page-evidence.json"),
    JSON.stringify(evidence, null, 2),
    "utf8"
  );
  await miniProgram.disconnect();
  console.log(JSON.stringify({ task_id: evidence.task_id, assertions: evidence.assertions }));
})().catch((error) => {
  fs.writeFileSync(
    path.join(OUT, "page-flow-error.txt"),
    `${error && error.message}\n${error && error.stack}\n`,
    "utf8"
  );
  console.error(error && error.message);
  process.exit(1);
});
