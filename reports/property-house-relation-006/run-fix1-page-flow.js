"use strict";

const crypto = require("crypto");
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
const PRIOR = JSON.parse(fs.readFileSync(path.join(OUT, "page-evidence.json"), "utf8"));
const RUN_ID = `property-house-fix1-page-${Date.now().toString(36)}`;

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

function supervisorPhone() {
  if (!CODE_SHA) throw new Error("CODE_SHA is required");
  const prefix = crypto.createHash("sha256").update(CODE_SHA, "ascii").digest("hex").slice(0, 8);
  const digits = Number.parseInt(prefix, 16) % 100000000;
  return `+86138${String(digits).padStart(8, "0")}`;
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
  const inputs = await page.$$("input");
  for (const input of inputs) {
    if ((await input.attribute("placeholder")) === placeholder) {
      await input.tap();
      await input.input(String(value));
      return;
    }
  }
  throw new Error(`input not found: ${placeholder}`);
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
  const code = await developmentCode(phone);
  page = await current(miniProgram);
  await fill(page, "短信验证码", code);
  await tapButton(page, "登录");
  return waitPath(miniProgram, "pages/home/home");
}

async function openArchive(miniProgram) {
  const home = await waitPath(miniProgram, "pages/home/home");
  await tapButton(home, "楼栋房屋与住户关系");
  return waitUntil(async () => {
    const page = await current(miniProgram);
    if (!page || page.path !== "pages/supervisor/properties") return null;
    const state = await page.data();
    return !state.loading && state.properties.length ? page : null;
  }, "property archive page");
}

async function screenshotAtButton(miniProgram, page, label, name) {
  await miniProgram.callWxMethod("pageScrollTo", { scrollTop: 0, duration: 0 });
  await wait(300);
  const buttons = await page.$$("button");
  for (const button of buttons) {
    if (((await button.text()) || "").trim() === label) {
      const offset = await button.offset();
      await miniProgram.callWxMethod("pageScrollTo", {
        scrollTop: Math.max(0, Number(offset.top) - 650),
        duration: 0,
      });
      await wait(500);
      await miniProgram.screenshot({ path: path.join(OUT, name) });
      return;
    }
  }
  throw new Error(`screenshot anchor not found: ${label}`);
}

(async () => {
  const miniProgram = await automator.connect({ wsEndpoint: WS });
  let page = await smsLogin(miniProgram, ACCOUNTS.phones.property_admin);
  page = await openArchive(miniProgram);
  let state = await page.data();
  if (!state.canEdit) throw new Error("PROPERTY_ADMIN archive is unexpectedly read-only");

  const buildingCode = PRIOR.created_synthetic_codes.imported_building_code;
  const unitCode = PRIOR.created_synthetic_codes.imported_unit_code;
  const building = state.buildings.find((item) => item.code === buildingCode);
  const unit = state.units.find((item) => item.code === unitCode);
  if (!building || !unit) throw new Error("prior synthetic archive targets are missing");
  await tapContains(page, ".archive-card", buildingCode);
  await waitUntil(async () => {
    const data = await (await current(miniProgram)).data();
    return data.selectedBuilding && data.selectedBuilding.id === building.id;
  }, "building selection");
  page = await current(miniProgram);
  state = await page.data();
  const buildingOriginal = state.selectedBuilding.name;
  const buildingEdited = `${buildingOriginal}·页面`;
  await fill(page, "楼栋名称（维护）", buildingEdited);
  await tapButton(page, "保存楼栋档案");
  await waitUntil(async () => {
    const candidate = await current(miniProgram);
    const data = await candidate.data();
    return data.buildings.some((item) => item.id === building.id && item.name === buildingEdited)
      ? candidate
      : null;
  }, "building page update");
  page = await current(miniProgram);
  await screenshotAtButton(miniProgram, page, "保存楼栋档案", "fix1-01-building-edit.png");
  await fill(page, "楼栋名称（维护）", buildingOriginal);
  await tapButton(page, "保存楼栋档案");
  await waitUntil(async () => {
    const data = await (await current(miniProgram)).data();
    return data.buildings.some((item) => item.id === building.id && item.name === buildingOriginal);
  }, "building page restore");

  page = await current(miniProgram);
  await tapContains(page, ".archive-card", unitCode);
  await waitUntil(async () => {
    const data = await (await current(miniProgram)).data();
    return data.selectedUnit && data.selectedUnit.id === unit.id;
  }, "unit selection");
  page = await current(miniProgram);
  state = await page.data();
  const unitOriginal = state.selectedUnit.name;
  const unitEdited = `${unitOriginal}·页面`;
  await fill(page, "单元名称（维护）", unitEdited);
  await tapButton(page, "保存单元档案");
  await waitUntil(async () => {
    const candidate = await current(miniProgram);
    const data = await candidate.data();
    return data.units.some((item) => item.id === unit.id && item.name === unitEdited)
      ? candidate
      : null;
  }, "unit page update");
  page = await current(miniProgram);
  await screenshotAtButton(miniProgram, page, "保存单元档案", "fix1-02-unit-edit.png");
  await fill(page, "单元名称（维护）", unitOriginal);
  await tapButton(page, "保存单元档案");
  await waitUntil(async () => {
    const data = await (await current(miniProgram)).data();
    return data.units.some((item) => item.id === unit.id && item.name === unitOriginal);
  }, "unit page restore");

  page = await smsLogin(miniProgram, supervisorPhone());
  const home = await page.data();
  if (!home.canViewProperty) throw new Error("SUPERVISOR home lacks the read-only entry");
  page = await openArchive(miniProgram);
  state = await page.data();
  const buttonLabels = [];
  for (const button of await page.$$("button")) {
    buttonLabels.push(((await button.text()) || "").trim());
  }
  const forbidden = [
    "建立楼栋",
    "建立单元",
    "建立房屋档案",
    "导入 CSV 内容",
    "保存楼栋档案",
    "保存单元档案",
    "保存房屋档案",
    "结束关系并保留历史",
  ];
  if (state.canEdit || forbidden.some((label) => buttonLabels.indexOf(label) >= 0)) {
    throw new Error("SUPERVISOR page exposed a write control");
  }
  await miniProgram.callWxMethod("pageScrollTo", { scrollTop: 0, duration: 0 });
  await wait(500);
  await miniProgram.screenshot({ path: path.join(OUT, "fix1-03-supervisor-readonly.png") });

  const evidence = {
    task_id: "PROPERTY-HOUSE-RELATION-006-FIX-1",
    run_id: RUN_ID,
    code_sha_product: CODE_SHA,
    environment: "WeChat DevTools automator -> 127.0.0.1:8083/api/v1 -> isolated 0009",
    interaction: "actual mini-program controls with official SMS sessions",
    actors: {
      manager: "PROPERTY_ADMIN; non-SYSTEM",
      read_only: "SUPERVISOR; development fixture; non-SYSTEM",
      raw_phones_saved: false,
      tokens_saved: false,
    },
    assertions: {
      manager_updated_building_through_page: true,
      manager_restored_building_name_through_page: true,
      manager_updated_unit_through_page: true,
      manager_restored_unit_name_through_page: true,
      supervisor_entry_visible_from_home: true,
      supervisor_archive_loaded: state.buildings.length > 0,
      supervisor_can_edit_false: state.canEdit === false,
      supervisor_write_controls_absent: true,
    },
    supervisor_visible_buttons: buttonLabels,
    observed_counts: {
      buildings: state.buildings.length,
      units: state.units.length,
      houses: state.houses.length,
      relations: state.relations.length,
    },
    screenshots: [
      "fix1-01-building-edit.png",
      "fix1-02-unit-edit.png",
      "fix1-03-supervisor-readonly.png",
    ],
    not_claimed: ["real SMS", "production", "Production V1 acceptance"],
  };
  fs.writeFileSync(
    path.join(OUT, "fix1-page-evidence.json"),
    JSON.stringify(evidence, null, 2),
    "utf8"
  );
  await miniProgram.disconnect();
  console.log(JSON.stringify({ task_id: evidence.task_id, assertions: evidence.assertions }));
})().catch((error) => {
  fs.writeFileSync(
    path.join(OUT, "fix1-page-error.txt"),
    `${error && error.message}\n${error && error.stack}\n`,
    "utf8"
  );
  console.error(error && error.message);
  process.exit(1);
});
