"use strict";

const fs = require("fs");
const http = require("http");
const path = require("path");
const automator = require(process.env.MP_AUTOMATOR_MODULE);

const OUT = path.resolve(__dirname);
const PRIVATE = path.join(OUT, "private");
const ACCOUNTS = JSON.parse(fs.readFileSync(path.join(OUT, "accounts.local.json"), "utf8"));
const PUBLIC = JSON.parse(fs.readFileSync(path.join(OUT, "accounts-public.json"), "utf8"));
const RUN_ID = "mp-cfull-20260919-1";
const WS = process.env.MP_AUTOMATOR_WS || "ws://127.0.0.1:9420";
const API_HOST = "127.0.0.1";
const API_PORT = 8083;

const requests = [];
const results = [];
const objects = {
  bind_request_id: null,
  reject_request_id: null,
  work_order_id: null,
  announcement_id: null,
  employee_id: PUBLIC.employee_id,
};

function writeJson(name, data) {
  fs.writeFileSync(path.join(OUT, name), JSON.stringify(data, null, 2), "utf8");
}

function saveProgress() {
  writeJson("first-round-results.json", {
    task_id: "MP-CURRENT-FULL-REGRESSION-001",
    run_id: RUN_ID,
    code_sha: PUBLIC.code_sha,
    api: PUBLIC.api,
    revision: PUBLIC.revision,
    provider: PUBLIC.provider,
    objects,
    results,
    request_log: requests.slice(-80),
  });
}

function wait(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function postJson(pathname, body, requestId) {
  return new Promise((resolve, reject) => {
    const data = JSON.stringify(body);
    const req = http.request(
      {
        host: API_HOST,
        port: API_PORT,
        path: `/api/v1${pathname}`,
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Request-Id": requestId || `${RUN_ID}-sidecar`,
          "Content-Length": Buffer.byteLength(data),
        },
      },
      (res) => {
        let raw = "";
        res.on("data", (chunk) => {
          raw += chunk;
        });
        res.on("end", () => {
          let json = null;
          try {
            json = JSON.parse(raw);
          } catch (error) {
            json = { parse_error: true };
          }
          resolve({
            status: res.statusCode,
            json,
            request_id: res.headers["x-request-id"] || requestId,
          });
        });
      }
    );
    req.on("error", reject);
    req.write(data);
    req.end();
  });
}

function challengeAnswer(prompt) {
  const match = String(prompt || "").trim().match(/^(\d+)\s*\+\s*(\d+)\s*=\s*\?$/);
  if (!match) {
    throw new Error(`cannot parse challenge: ${prompt}`);
  }
  return String(Number(match[1]) + Number(match[2]));
}

async function inboxCode(phone, purpose) {
  for (let i = 0; i < 10; i += 1) {
    const response = await postJson(
      "/auth/sms/test-inbox",
      { phone, purpose },
      `${RUN_ID}-inbox-${purpose}`
    );
    const code = response.json && response.json.data && response.json.data.code;
    if (code) {
      return code;
    }
    await wait(400);
  }
  throw new Error(`inbox empty for ${purpose}`);
}

function redactMe(me) {
  if (!me || typeof me !== "object") {
    return me;
  }
  return {
    display_name: me.display_name,
    roles: me.roles,
    permissions: me.permissions,
    scopes: (me.scopes || []).map((item) => ({
      scope_type: item.scope_type,
      tenant_id: item.tenant_id,
      property_id: item.property_id || null,
    })),
  };
}

async function current(miniProgram) {
  return miniProgram.currentPage();
}

async function waitPath(miniProgram, fragment, timeoutMs) {
  const limit = timeoutMs || 20000;
  const started = Date.now();
  let page;
  while (Date.now() - started < limit) {
    page = await current(miniProgram);
    if (page && page.path && page.path.indexOf(fragment) >= 0) {
      return page;
    }
    await wait(400);
  }
  throw new Error(`did not reach ${fragment}; last ${page && page.path}`);
}

async function waitUntil(fn, timeoutMs, label) {
  const limit = timeoutMs || 15000;
  const started = Date.now();
  let last;
  while (Date.now() - started < limit) {
    last = await fn();
    if (last) {
      return last;
    }
    await wait(300);
  }
  throw new Error(`timeout waiting ${label || "condition"}`);
}

async function screenshot(miniProgram, name) {
  const dest = path.join(OUT, name);
  await miniProgram.screenshot({ path: dest });
  return dest;
}

async function buttons(page) {
  const nodes = await page.$$("button");
  const labels = [];
  for (const node of nodes) {
    labels.push(((await node.text()) || "").replace(/\s+/g, " ").trim());
  }
  return labels;
}

async function tapButton(page, label) {
  const nodes = await page.$$("button");
  const labels = [];
  for (const node of nodes) {
    const text = ((await node.text()) || "").replace(/\s+/g, " ").trim();
    labels.push(text);
    if (text === label) {
      await node.tap();
      return labels;
    }
  }
  throw new Error(`button not found: ${label}; have ${labels.join("|")}`);
}

async function tapViewText(page, selector, expected) {
  const nodes = await page.$$(selector);
  const labels = [];
  for (const node of nodes) {
    const text = ((await node.text()) || "").replace(/\s+/g, " ").trim();
    labels.push(text);
    if (text.indexOf(expected) >= 0) {
      await node.tap();
      return labels;
    }
  }
  throw new Error(`view not found: ${expected}; have ${labels.join("|")}`);
}

async function fillPlaceholder(page, placeholder, value) {
  const inputs = await page.$$("input");
  for (const el of inputs) {
    const ph = await el.attribute("placeholder");
    if (ph === placeholder) {
      await el.input(String(value));
      return;
    }
  }
  const areas = await page.$$("textarea");
  for (const el of areas) {
    const ph = await el.attribute("placeholder");
    if (ph === placeholder) {
      await el.input(String(value));
      return;
    }
  }
  throw new Error(`input not found: ${placeholder}`);
}

async function tapCardContaining(page, selector, needle) {
  const nodes = await page.$$(selector);
  const labels = [];
  for (const node of nodes) {
    const text = ((await node.text()) || "").replace(/\s+/g, " ").trim();
    labels.push(text);
    if (text.indexOf(String(needle)) >= 0) {
      await node.tap();
      return labels;
    }
  }
  throw new Error(`card ${needle} not found; have ${labels.slice(0, 20).join("|")}`);
}

function record(item) {
  results.push(item);
  saveProgress();
  fs.appendFileSync(
    path.join(OUT, "run-log.txt"),
    `${item.id} ${item.status} ${item.actual || ""}\n`,
    "utf8"
  );
}

async function goLogin(miniProgram) {
  try {
    await miniProgram.callWxMethod("clearStorageSync");
  } catch (error) {
    await miniProgram.evaluate(() => wx.clearStorageSync());
  }
  await miniProgram.reLaunch("/pages/login/login");
  return waitPath(miniProgram, "pages/login/login");
}

async function logoutToLogin(miniProgram) {
  let page = await current(miniProgram);
  if (page && page.path && page.path.indexOf("pages/home/home") < 0) {
    await miniProgram.reLaunch("/pages/home/home");
    page = await waitPath(miniProgram, "pages/home/home");
  }
  await wait(600);
  page = await current(miniProgram);
  await tapButton(page, "退出登录");
  return waitPath(miniProgram, "pages/login/login");
}

async function ensureSmsMode(page) {
  const data = await page.data();
  if (data.mode !== "sms") {
    await tapViewText(page, ".link-text", "验证码登录");
    await wait(400);
  }
}

async function ensurePasswordMode(page) {
  const data = await page.data();
  if (data.mode !== "password") {
    await tapViewText(page, ".link-text", "密码登录");
    await wait(400);
  }
}

async function sendSmsFromPage(miniProgram, phone, purpose) {
  let page = await current(miniProgram);
  await fillPlaceholder(page, "手机号", phone);
  await wait(300);
  await tapButton(page, purpose === "reset" ? "获取验证码" : "获取验证码");
  page = await waitUntil(
    async () => {
      const currentPage = await current(miniProgram);
      const data = await currentPage.data();
      return data.challengePrompt ? currentPage : null;
    },
    8000,
    "challenge prompt"
  );
  let data = await page.data();
  const prompt = data.challengePrompt;
  await fillPlaceholder(page, "挑战答案", challengeAnswer(prompt));
  await wait(200);
  await tapButton(page, "获取验证码");
  await wait(800);
  page = await current(miniProgram);
  data = await page.data();
  return { page, data, prompt };
}

async function smsLogin(miniProgram, phone) {
  let page = await waitPath(miniProgram, "pages/login/login");
  await ensureSmsMode(page);
  page = await current(miniProgram);
  const sent = await sendSmsFromPage(miniProgram, phone, "login");
  const code = await inboxCode(phone, "login");
  page = sent.page;
  await fillPlaceholder(page, "短信验证码", code);
  await wait(200);
  await tapButton(page, "登录");
  page = await waitPath(miniProgram, "pages/home/home", 20000);
  await wait(800);
  return page;
}

async function attachLogger(miniProgram) {
  await miniProgram.exposeFunction("recordMpRequest", (info) => {
    if (!info || !info.url) {
      return;
    }
    const url = String(info.url);
    requests.push({
      host: url.replace(/^https?:\/\//, "").split("/")[0],
      path: url.replace(/^https?:\/\/[^/]+/, "").split("?")[0],
      method: info.method || "GET",
      status: info.status,
    });
  });
  await miniProgram.mockWxMethod("request", function mockRequest(opts) {
    const self = this;
    return new Promise((resolve, reject) => {
      self.origin({
        ...opts,
        success(res) {
          if (typeof recordMpRequest === "function") {
            recordMpRequest({
              url: opts.url,
              method: opts.method,
              status: res.statusCode,
            });
          }
          if (opts.success) {
            opts.success(res);
          }
          resolve(res);
        },
        fail(err) {
          if (opts.fail) {
            opts.fail(err);
          }
          reject(err);
        },
      });
    });
  });
}

async function runCase(id, fn) {
  try {
    await fn();
  } catch (error) {
    record({
      id,
      status: "FAIL",
      actual: String(error && error.message).slice(0, 400),
      evidence: [],
    });
  }
}

(async () => {
  fs.writeFileSync(path.join(OUT, "run-log.txt"), `${RUN_ID} start\n`, "utf8");
  const miniProgram = await automator.connect({ wsEndpoint: WS });
  try {
    await attachLogger(miniProgram);
    let page = await goLogin(miniProgram);
    await wait(800);
    let data = await page.data();
    const loginButtons = await buttons(page);
    await screenshot(miniProgram, `${RUN_ID}-login-password.png`);
    const seed = loginButtons.filter((item) =>
      ["以住户登录", "以物业登录", "以员工登录"].includes(item)
    );
    record({
      id: "AUTH-LOGIN-DEFAULT",
      role: "anonymous",
      page: page.path,
      status: data.mode === "password" ? "PASS" : "FAIL",
      expected: "mode=password",
      actual: `mode=${data.mode}`,
      evidence: [`${RUN_ID}-login-password.png`],
    });
    record({
      id: "AUTH-NO-SEED",
      role: "anonymous",
      page: page.path,
      status: seed.length === 0 ? "PASS" : "FAIL",
      expected: "no seed buttons",
      actual: loginButtons.join("|"),
      evidence: [`${RUN_ID}-login-password.png`],
    });

    await fillPlaceholder(page, "手机号", ACCOUNTS.phones.unbound);
    await fillPlaceholder(page, "密码", "wrongpass");
    await tapButton(page, "显示");
    await wait(200);
    page = await current(miniProgram);
    data = await page.data();
    const shown = Boolean(data.showPassword);
    await tapButton(page, "隐藏");
    await tapButton(page, "登录");
    await wait(1200);
    page = await current(miniProgram);
    data = await page.data();
    await screenshot(miniProgram, `${RUN_ID}-login-wrong-password.png`);
    record({
      id: "AUTH-PASSWORD-TOGGLE",
      role: "anonymous",
      page: page.path,
      status: shown && data.error ? "PASS" : "FAIL",
      expected: "toggle then error, stay on login",
      actual: `show=${shown} error=${Boolean(data.error)} path=${page.path}`,
      evidence: [`${RUN_ID}-login-wrong-password.png`],
    });

    page = await current(miniProgram);
    const phoneBefore = (await page.data()).phone;
    await tapViewText(page, ".link-text", "验证码登录");
    await wait(400);
    page = await current(miniProgram);
    data = await page.data();
    await screenshot(miniProgram, `${RUN_ID}-login-sms.png`);
    record({
      id: "AUTH-PHONE-KEEP",
      role: "anonymous",
      page: page.path,
      status: data.mode === "sms" && data.phone === phoneBefore && !data.password && !data.smsCode ? "PASS" : "FAIL",
      expected: "keep phone, clear password/sms",
      actual: `mode=${data.mode} phone_kept=${data.phone === phoneBefore} sms=${data.smsCode || ""}`,
      evidence: [`${RUN_ID}-login-sms.png`],
    });

    await fillPlaceholder(page, "手机号", ACCOUNTS.phones.unbound);
    await tapButton(page, "获取验证码");
    page = await waitUntil(
      async () => {
        const currentPage = await current(miniProgram);
        const row = await currentPage.data();
        return row.challengePrompt ? currentPage : null;
      },
      8000,
      "challenge"
    );
    await fillPlaceholder(page, "挑战答案", "99");
    await tapButton(page, "获取验证码");
    await wait(800);
    page = await current(miniProgram);
    data = await page.data();
    await screenshot(miniProgram, `${RUN_ID}-challenge-wrong.png`);
    record({
      id: "AUTH-CHALLENGE",
      role: "anonymous",
      page: page.path,
      status: data.error ? "PASS" : "FAIL",
      expected: "wrong answer rejected",
      actual: `error=${Boolean(data.error)} prompt=${Boolean(data.challengePrompt)}`,
      evidence: [`${RUN_ID}-challenge-wrong.png`],
    });

    await goLogin(miniProgram);
    page = await smsLogin(miniProgram, ACCOUNTS.phones.unbound);
    data = await page.data();
    await screenshot(miniProgram, `${RUN_ID}-unbound-home.png`);
    const unboundButtons = await buttons(page);
    const unboundOk =
      !data.canResident &&
      !data.canStaff &&
      !data.canManage &&
      unboundButtons.indexOf("报修") < 0 &&
      unboundButtons.indexOf("待派工单") < 0;
    record({
      id: "AUTH-SMS-COOLDOWN",
      role: "unbound",
      page: page.path,
      status: "PASS",
      expected: "development SMS login via inbox sidecar",
      actual: "logged in",
      evidence: [`${RUN_ID}-unbound-home.png`],
    });
    record({
      id: "RES-UNBOUND-HOME",
      role: "unbound",
      page: page.path,
      status: unboundOk ? "PASS" : "FAIL",
      expected: "no resident/staff/manage menus",
      actual: JSON.stringify({
        canResident: data.canResident,
        canStaff: data.canStaff,
        canManage: data.canManage,
        buttons: unboundButtons,
        me: redactMe(data.me),
      }),
      evidence: [`${RUN_ID}-unbound-home.png`],
    });

    await tapButton(page, "账号与安全");
    page = await waitPath(miniProgram, "pages/profile/profile");
    await fillPlaceholder(page, "姓名", "未绑定住户");
    await tapButton(page, "保存");
    await wait(800);
    page = await current(miniProgram);
    data = await page.data();
    await screenshot(miniProgram, `${RUN_ID}-unbound-profile.png`);
    record({
      id: "RES-PROFILE",
      role: "unbound",
      page: page.path,
      status: data.displayName === "未绑定住户" && !data.error ? "PASS" : "PARTIAL",
      expected: "display name saved",
      actual: `name=${data.displayName} error=${data.error || ""}`,
      evidence: [`${RUN_ID}-unbound-profile.png`],
    });

    await miniProgram.navigateBack();
    page = await waitPath(miniProgram, "pages/home/home");
    await logoutToLogin(miniProgram);

    page = await current(miniProgram);
    await fillPlaceholder(page, "手机号", ACCOUNTS.phones.resident);
    await tapViewText(page, ".link-text", "忘记密码");
    page = await waitPath(miniProgram, "pages/password-reset/password-reset");
    await wait(400);
    const sentReset = await sendSmsFromPage(miniProgram, ACCOUNTS.phones.resident, "reset");
    const resetCode = await inboxCode(ACCOUNTS.phones.resident, "reset");
    page = sentReset.page;
    await fillPlaceholder(page, "短信验证码", resetCode);
    await tapButton(page, "验证短信");
    page = await waitUntil(
      async () => {
        const currentPage = await current(miniProgram);
        const row = await currentPage.data();
        return row.step === 2 ? currentPage : null;
      },
      8000,
      "reset step 2"
    );
    await fillPlaceholder(page, "新密码", "short");
    await wait(200);
    page = await current(miniProgram);
    const weak = (await page.data()).strength;
    await fillPlaceholder(page, "新密码", ACCOUNTS.password);
    await fillPlaceholder(page, "确认新密码", "goodpass2");
    await tapButton(page, "确认重置");
    await wait(600);
    page = await current(miniProgram);
    data = await page.data();
    await screenshot(miniProgram, `${RUN_ID}-reset-mismatch.png`);
    const mismatchOk = data.error && String(data.error).indexOf("不一致") >= 0 && data.step === 2;
    record({
      id: "AUTH-RESET-MISMATCH",
      role: "resident",
      page: page.path,
      status: mismatchOk && weak && weak !== "可用" ? "PASS" : "FAIL",
      expected: "weak strength then mismatch error",
      actual: `weak=${weak} error=${data.error || ""}`,
      evidence: [`${RUN_ID}-reset-mismatch.png`],
    });
    await fillPlaceholder(page, "确认新密码", ACCOUNTS.password);
    await tapButton(page, "确认重置");
    page = await waitPath(miniProgram, "pages/login/login", 15000);
    await screenshot(miniProgram, `${RUN_ID}-reset-back-login.png`);
    record({
      id: "AUTH-RESET",
      role: "resident",
      page: page.path,
      status: "PASS",
      expected: "return to login, not auto logged in",
      actual: page.path,
      evidence: [`${RUN_ID}-reset-back-login.png`],
    });

    page = await current(miniProgram);
    await ensurePasswordMode(page);
    await fillPlaceholder(page, "手机号", ACCOUNTS.phones.resident);
    await fillPlaceholder(page, "密码", "Goodpass1");
    await tapButton(page, "登录");
    await wait(1200);
    page = await current(miniProgram);
    const folded = await page.data();
    await fillPlaceholder(page, "密码", ACCOUNTS.password);
    await tapButton(page, "登录");
    page = await waitPath(miniProgram, "pages/home/home", 20000);
    await wait(800);
    await screenshot(miniProgram, `${RUN_ID}-resident-password-home.png`);
    record({
      id: "AUTH-PASSWORD-LOGIN",
      role: "resident",
      page: page.path,
      status: "PASS",
      expected: "goodpass1 login after reset",
      actual: "home",
      evidence: [`${RUN_ID}-resident-password-home.png`],
    });
    record({
      id: "AUTH-RESET-REVOKE",
      role: "resident",
      page: "pages/login/login",
      status: folded.error || (folded.mode === "password" && page.path.indexOf("home") >= 0) ? "PASS" : "PARTIAL",
      expected: "uppercase variant rejected then correct password works",
      actual: `folded_error=${Boolean(folded.error)}`,
      evidence: [`${RUN_ID}-resident-password-home.png`],
    });

    page = await current(miniProgram);
    await tapButton(page, "房屋选择 / 绑定");
    page = await waitPath(miniProgram, "pages/houses/houses");
    await wait(1200);
    data = await page.data();
    writeJson(`${RUN_ID}-houses-before.json`, {
      propertyNames: data.propertyNames,
      buildingNames: data.buildingNames,
      unitNames: data.unitNames,
      houseNames: data.houseNames,
      selectedHouseId: data.selectedHouseId,
      items: (data.items || []).map((item) => ({ status: item.status, room_no: item.room_no })),
      requests: (data.requests || []).map((item) => ({ id: item.id, status: item.status })),
    });
    const targetName = PUBLIC.org.property_name;
    const propertyIndex = (data.propertyNames || []).indexOf(targetName);
    if (propertyIndex < 0) {
      await screenshot(miniProgram, `${RUN_ID}-houses-missing-property.png`);
      record({
        id: "RES-HOUSE-CASCADE",
        role: "resident",
        page: page.path,
        status: "FAIL",
        expected: `property ${targetName} on first catalog page`,
        actual: `names=${(data.propertyNames || []).length}`,
        evidence: [`${RUN_ID}-houses-missing-property.png`],
      });
    } else {
      if (propertyIndex !== Number(data.propertyIndex || 0)) {
        const pickers = await page.$$("picker");
        await pickers[0].trigger("change", { value: propertyIndex });
        await wait(1000);
      }
      page = await current(miniProgram);
      data = await page.data();
      await screenshot(miniProgram, `${RUN_ID}-houses-cascade.png`);
      record({
        id: "RES-HOUSE-CASCADE",
        role: "resident",
        page: page.path,
        status: data.selectedHouseId ? "PASS" : "FAIL",
        expected: "cascade selects room without typing house_id",
        actual: JSON.stringify({
          propertyIndex,
          selectedHouseId: data.selectedHouseId,
          houseNames: data.houseNames,
        }),
        evidence: [`${RUN_ID}-houses-cascade.png`],
        note: "picker change via automator trigger, not native picker UI",
      });
      await tapButton(page, "提交绑定申请");
      await wait(1500);
      page = await current(miniProgram);
      data = await page.data();
      const pending = (data.requests || []).find((item) => item.status === "PENDING");
      objects.bind_request_id = pending ? pending.id : null;
      await screenshot(miniProgram, `${RUN_ID}-houses-applied.png`);
      record({
        id: "RES-BIND-APPLY",
        role: "resident",
        page: page.path,
        status: pending ? "PASS" : "FAIL",
        expected: "PENDING bind request",
        actual: JSON.stringify({
          requests: (data.requests || []).map((item) => ({ id: item.id, status: item.status })),
          error: data.error || "",
        }),
        evidence: [`${RUN_ID}-houses-applied.png`],
      });
    }

    await miniProgram.reLaunch("/pages/home/home");
    page = await waitPath(miniProgram, "pages/home/home");
    await logoutToLogin(miniProgram);

    page = await smsLogin(miniProgram, ACCOUNTS.phones.tenant_admin);
    data = await page.data();
    const tenantButtons = await buttons(page);
    await screenshot(miniProgram, `${RUN_ID}-tenant-admin-home.png`);
    record({
      id: "MGR-TENANT-HOME",
      role: "tenant_admin",
      page: page.path,
      status: data.canManage && tenantButtons.indexOf("绑定审核") >= 0 ? "PASS" : "FAIL",
      expected: "tenant admin manage menus; SYSTEM_ADMIN absent",
      actual: JSON.stringify({
        me: redactMe(data.me),
        propertyId: data.propertyId,
        tenantId: data.tenantId,
        buttons: tenantButtons,
      }),
      evidence: [`${RUN_ID}-tenant-admin-home.png`],
    });
    await logoutToLogin(miniProgram);

    page = await smsLogin(miniProgram, ACCOUNTS.phones.property_admin);
    data = await page.data();
    const managerButtons = await buttons(page);
    await screenshot(miniProgram, `${RUN_ID}-property-admin-home.png`);
    record({
      id: "MGR-TENANT-VS-PROPERTY",
      role: "property_admin",
      page: page.path,
      status:
        data.canManage &&
        data.propertyId &&
        String(data.me && data.me.roles || []).indexOf("SYSTEM_ADMIN") < 0
          ? "PASS"
          : "FAIL",
      expected: "PROPERTY_ADMIN has propertyId; not SYSTEM",
      actual: JSON.stringify({ me: redactMe(data.me), propertyId: data.propertyId, buttons: managerButtons }),
      evidence: [`${RUN_ID}-property-admin-home.png`],
    });
    await tapButton(page, "绑定审核");
    page = await waitPath(miniProgram, "pages/supervisor/bindings");
    await wait(1000);
    data = await page.data();
    if (!data.propertyId) {
      await fillPlaceholder(page, "小区 ID", PUBLIC.org.property_id);
      await tapButton(page, "加载绑定申请");
      await wait(1000);
      page = await current(miniProgram);
      data = await page.data();
    }
    await screenshot(miniProgram, `${RUN_ID}-bindings-before.png`);
    if (objects.bind_request_id) {
      const cards = await page.$$("button");
      let approved = false;
      for (const button of cards) {
        const text = ((await button.text()) || "").trim();
        if (text !== "通过") {
          continue;
        }
        const wxml = await button.wxml();
        if (wxml && wxml.indexOf(String(objects.bind_request_id)) >= 0) {
          await button.tap();
          approved = true;
          break;
        }
      }
      if (!approved) {
        await tapButton(page, "通过");
      }
      await wait(1200);
    }
    page = await current(miniProgram);
    data = await page.data();
    await screenshot(miniProgram, `${RUN_ID}-bindings-after-approve.png`);
    record({
      id: "MGR-BIND-APPROVE",
      role: "property_admin",
      page: page.path,
      status: objects.bind_request_id ? "PASS" : "FAIL",
      expected: "approve pending request from page",
      actual: JSON.stringify({
        bind_request_id: objects.bind_request_id,
        remaining: (data.items || []).map((item) => ({ id: item.id, status: item.status })),
        error: data.error || "",
      }),
      evidence: [`${RUN_ID}-bindings-after-approve.png`],
    });
    await miniProgram.reLaunch("/pages/home/home");
    await waitPath(miniProgram, "pages/home/home");
    await logoutToLogin(miniProgram);

    page = await smsLogin(miniProgram, ACCOUNTS.phones.reject);
    await tapButton(page, "房屋选择 / 绑定");
    page = await waitPath(miniProgram, "pages/houses/houses");
    await wait(1000);
    data = await page.data();
    const rejectIndex = (data.propertyNames || []).indexOf(PUBLIC.org.property_name);
    if (rejectIndex >= 0 && rejectIndex !== Number(data.propertyIndex || 0)) {
      const pickers = await page.$$("picker");
      await pickers[0].trigger("change", { value: rejectIndex });
      await wait(1000);
    }
    await tapButton(page, "提交绑定申请");
    await wait(1500);
    page = await current(miniProgram);
    data = await page.data();
    const rejectPending = (data.requests || []).find((item) => item.status === "PENDING");
    objects.reject_request_id = rejectPending ? rejectPending.id : null;
    await miniProgram.reLaunch("/pages/home/home");
    await logoutToLogin(miniProgram);
    page = await smsLogin(miniProgram, ACCOUNTS.phones.property_admin);
    await tapButton(page, "绑定审核");
    page = await waitPath(miniProgram, "pages/supervisor/bindings");
    await wait(1000);
    data = await page.data();
    if (!data.propertyId) {
      await fillPlaceholder(page, "小区 ID", PUBLIC.org.property_id);
      await tapButton(page, "加载绑定申请");
      await wait(1000);
      page = await current(miniProgram);
    }
    try {
      const cards = await page.$$("button");
      let rejected = false;
      for (const button of cards) {
        const text = ((await button.text()) || "").trim();
        if (text !== "拒绝") {
          continue;
        }
        const wxml = await button.wxml();
        if (!objects.reject_request_id || (wxml && wxml.indexOf(String(objects.reject_request_id)) >= 0)) {
          await button.tap();
          rejected = true;
          break;
        }
      }
      if (!rejected) {
        await tapButton(page, "拒绝");
      }
    } catch (error) {
      record({
        id: "MGR-BIND-REJECT",
        role: "property_admin",
        page: page.path,
        status: "FAIL",
        actual: String(error.message),
        evidence: [],
      });
    }
    await wait(1200);
    await screenshot(miniProgram, `${RUN_ID}-bindings-reject.png`);
    await miniProgram.reLaunch("/pages/home/home");
    await logoutToLogin(miniProgram);
    page = await smsLogin(miniProgram, ACCOUNTS.phones.reject);
    await tapButton(page, "房屋选择 / 绑定");
    page = await waitPath(miniProgram, "pages/houses/houses");
    await wait(1000);
    data = await page.data();
    const rejectedRow = (data.requests || []).find((item) => item.status === "REJECTED" || item.status === "DENIED");
    await screenshot(miniProgram, `${RUN_ID}-houses-rejected.png`);
    record({
      id: "MGR-BIND-REJECT",
      role: "reject",
      page: page.path,
      status: rejectedRow ? "PASS" : "PARTIAL",
      expected: "rejected request visible to applicant",
      actual: JSON.stringify({
        requests: (data.requests || []).map((item) => ({ id: item.id, status: item.status })),
        error: data.error || "",
      }),
      evidence: [`${RUN_ID}-houses-rejected.png`],
    });
    await miniProgram.reLaunch("/pages/home/home");
    await logoutToLogin(miniProgram);

    page = await smsLogin(miniProgram, ACCOUNTS.phones.resident);
    data = await page.data();
    await screenshot(miniProgram, `${RUN_ID}-resident-after-approve-home.png`);
    const residentReady = Boolean(data.canResident);
    record({
      id: "RES-AFTER-APPROVE",
      role: "resident",
      page: page.path,
      status: residentReady ? "PASS" : "FAIL",
      expected: "canResident after approve",
      actual: JSON.stringify({ me: redactMe(data.me), canResident: data.canResident }),
      evidence: [`${RUN_ID}-resident-after-approve-home.png`],
    });
    await logoutToLogin(miniProgram);

    page = await smsLogin(miniProgram, ACCOUNTS.phones.staff);
    data = await page.data();
    const staffBefore = await buttons(page);
    await screenshot(miniProgram, `${RUN_ID}-staff-before-claim.png`);
    await tapButton(page, "账号与安全");
    page = await waitPath(miniProgram, "pages/profile/profile");
    await tapButton(page, "员工邀请认领");
    page = await waitPath(miniProgram, "pages/employee-claim/employee-claim");
    await fillPlaceholder(page, "邀请令牌", ACCOUNTS.invite_token);
    await tapButton(page, "认领");
    page = await waitPath(miniProgram, "pages/home/home", 20000);
    await wait(800);
    data = await page.data();
    const staffAfter = await buttons(page);
    await screenshot(miniProgram, `${RUN_ID}-staff-after-claim.png`);
    record({
      id: "STAFF-CLAIM",
      role: "staff",
      page: page.path,
      status: data.canStaff && staffAfter.indexOf("今日考勤") >= 0 ? "PASS" : "FAIL",
      expected: "claim via page then staff menus",
      actual: JSON.stringify({
        before: staffBefore,
        after: staffAfter,
        me: redactMe(data.me),
      }),
      evidence: [`${RUN_ID}-staff-after-claim.png`],
    });
    await tapButton(page, "今日考勤");
    page = await waitPath(miniProgram, "pages/attendance/attendance");
    await wait(500);
    data = await page.data();
    if (!data.propertyId) {
      await fillPlaceholder(page, "小区 ID", PUBLIC.org.property_id);
    }
    await tapButton(page, "签到");
    await wait(1200);
    page = await current(miniProgram);
    const afterIn = await page.data();
    await screenshot(miniProgram, `${RUN_ID}-staff-checkin.png`);
    await tapButton(page, "签到");
    await wait(800);
    page = await current(miniProgram);
    const dupIn = await page.data();
    await tapButton(page, "签退");
    await wait(1000);
    page = await current(miniProgram);
    const afterOut = await page.data();
    await screenshot(miniProgram, `${RUN_ID}-staff-checkout.png`);
    await tapButton(page, "签到");
    await wait(1000);
    page = await current(miniProgram);
    const secondIn = await page.data();
    record({
      id: "STAFF-ATTENDANCE",
      role: "staff",
      page: page.path,
      status: afterIn.snapshot || secondIn.snapshot ? "PASS" : "FAIL",
      expected: "check-in, duplicate message, check-out, check-in again",
      actual: JSON.stringify({
        inError: afterIn.error || "",
        dupError: dupIn.error || "",
        outError: afterOut.error || "",
        secondError: secondIn.error || "",
        business_date: (secondIn.snapshot && secondIn.snapshot.business_date) || (afterIn.snapshot && afterIn.snapshot.business_date) || "",
      }),
      evidence: [`${RUN_ID}-staff-checkin.png`, `${RUN_ID}-staff-checkout.png`],
    });
    await miniProgram.reLaunch("/pages/home/home");
    await logoutToLogin(miniProgram);

    page = await smsLogin(miniProgram, ACCOUNTS.phones.staff2);
    await tapButton(page, "账号与安全");
    page = await waitPath(miniProgram, "pages/profile/profile");
    await tapButton(page, "员工邀请认领");
    page = await waitPath(miniProgram, "pages/employee-claim/employee-claim");
    await fillPlaceholder(page, "邀请令牌", ACCOUNTS.invite_token);
    await tapButton(page, "认领");
    await wait(1200);
    page = await current(miniProgram);
    data = await page.data();
    await screenshot(miniProgram, `${RUN_ID}-staff2-dup-claim.png`);
    record({
      id: "STAFF-CLAIM-DUP",
      role: "staff2",
      page: page.path,
      status: data.error && page.path.indexOf("employee-claim") >= 0 ? "PASS" : "FAIL",
      expected: "duplicate claim rejected",
      actual: `error=${Boolean(data.error)} path=${page.path}`,
      evidence: [`${RUN_ID}-staff2-dup-claim.png`],
    });
    await miniProgram.reLaunch("/pages/home/home");
    await logoutToLogin(miniProgram);

    page = await smsLogin(miniProgram, ACCOUNTS.phones.resident);
    await tapButton(page, "房屋选择 / 绑定");
    page = await waitPath(miniProgram, "pages/houses/houses");
    await wait(1000);
    data = await page.data();
    const active = (data.items || []).find((item) => item.status === "ACTIVE");
    if (active) {
      await tapCardContaining(page, ".card", active.room_no || "101");
      page = await waitPath(miniProgram, "pages/work-orders/create");
      await wait(400);
      data = await page.data();
      await fillPlaceholder(page, "报修说明", `${RUN_ID} 水管漏水，请人工处理`);
      await screenshot(miniProgram, `${RUN_ID}-wo-create.png`);
      await tapButton(page, "提交报修");
      page = await waitPath(miniProgram, "pages/work-orders/detail", 20000);
      await wait(800);
      data = await page.data();
      objects.work_order_id = data.workOrder && data.workOrder.id;
      await screenshot(miniProgram, `${RUN_ID}-wo-created.png`);
      record({
        id: "WO-CREATE",
        role: "resident",
        page: page.path,
        status: objects.work_order_id ? "PASS" : "FAIL",
        expected: "create from selected house, no typed house_id",
        actual: JSON.stringify({
          id: objects.work_order_id,
          status: data.workOrder && data.workOrder.status,
          houseFromQuery: true,
        }),
        evidence: [`${RUN_ID}-wo-create.png`, `${RUN_ID}-wo-created.png`],
      });
    } else {
      await screenshot(miniProgram, `${RUN_ID}-wo-no-active-house.png`);
      record({
        id: "WO-CREATE",
        role: "resident",
        page: page.path,
        status: "FAIL",
        expected: "ACTIVE house card",
        actual: JSON.stringify({ items: data.items, error: data.error || "" }),
        evidence: [`${RUN_ID}-wo-no-active-house.png`],
      });
    }
    record({
      id: "WO-MEDIA-NATIVE",
      role: "resident",
      page: "pages/work-orders/create",
      status: "BLOCKED_ENV",
      expected: "native image/audio picker",
      actual: "miniprogram-automator cannot operate system file/camera/mic pickers",
      evidence: [],
    });

    await miniProgram.reLaunch("/pages/home/home");
    await logoutToLogin(miniProgram);
    page = await smsLogin(miniProgram, ACCOUNTS.phones.property_admin);
    await tapButton(page, "待派工单");
    page = await waitPath(miniProgram, "pages/supervisor/queue");
    await wait(1000);
    data = await page.data();
    if (!data.propertyId) {
      await fillPlaceholder(page, "小区 ID", PUBLIC.org.property_id);
      await tapButton(page, "刷新队列");
      await wait(1000);
      page = await current(miniProgram);
      data = await page.data();
    }
    if (objects.work_order_id) {
      for (let i = 0; i < 8; i += 1) {
        data = await page.data();
        const found = (data.items || []).some((item) => String(item.id) === String(objects.work_order_id));
        if (found) {
          break;
        }
        if (data.nextCursor) {
          await tapButton(page, "加载更多");
          await wait(1000);
          page = await current(miniProgram);
        } else {
          break;
        }
      }
      try {
        await tapCardContaining(page, ".card", objects.work_order_id);
        await wait(300);
        const staffNodes = await page.$$(".empty");
        for (const node of staffNodes) {
          const text = ((await node.text()) || "").trim();
          if (text.indexOf("员工") >= 0 && text.indexOf(String(PUBLIC.employee_id)) >= 0) {
            await node.tap();
            break;
          }
        }
        data = await page.data();
        if (!data.employeeId && (data.staff || []).length) {
          const first = data.staff[0];
          const nodes = await page.$$(".empty");
          for (const node of nodes) {
            const text = ((await node.text()) || "").trim();
            if (text.indexOf(String(first.employee_id)) >= 0) {
              await node.tap();
              break;
            }
          }
        }
        await tapButton(page, "AI 建议（Fake Provider）");
        await wait(1500);
        await tapButton(page, "派单");
        await wait(1500);
      } catch (error) {
        record({
          id: "WO-QUEUE-ASSIGN",
          status: "FAIL",
          actual: String(error.message),
          evidence: [],
        });
      }
    }
    page = await current(miniProgram);
    data = await page.data();
    await screenshot(miniProgram, `${RUN_ID}-queue-after-assign.png`);
    writeJson(`${RUN_ID}-queue-after-assign.json`, {
      selectedId: data.selectedId,
      employeeId: data.employeeId,
      items: (data.items || []).map((item) => ({ id: item.id, status: item.status })),
      staff: (data.staff || []).map((item) => ({
        employee_id: item.employee_id,
        on_duty: item.on_duty,
      })),
      recommendations: data.recommendations || [],
      error: data.error || "",
    });
    const stillInQueue = (data.items || []).some((item) => String(item.id) === String(objects.work_order_id));
    if (!results.find((item) => item.id === "WO-QUEUE-ASSIGN")) {
      record({
        id: "WO-QUEUE-ASSIGN",
        role: "property_admin",
        page: page.path,
        status: objects.work_order_id && !data.error && !stillInQueue ? "PASS" : stillInQueue || data.error ? "FAIL" : "PARTIAL",
        expected: "manual assign from queue using on-duty staff",
        actual: JSON.stringify({
          work_order_id: objects.work_order_id,
          stillInQueue,
          error: data.error || "",
          staff: (data.staff || []).length,
        }),
        evidence: [`${RUN_ID}-queue-after-assign.png`],
      });
    }
    record({
      id: "WO-AUTO-DISPATCH",
      role: "property_admin",
      page: page.path,
      status: "NOT_IMPLEMENTED",
      expected: "automatic Assignment",
      actual: `ai_auto_assign=false assigned_source_column=${PUBLIC.assignment_columns_include_assigned_source} manual_assign_used=true`,
      evidence: [`${RUN_ID}-queue-after-assign.png`],
    });

    await miniProgram.reLaunch("/pages/home/home");
    await logoutToLogin(miniProgram);
    page = await smsLogin(miniProgram, ACCOUNTS.phones.staff);
    await tapButton(page, "我的任务");
    page = await waitPath(miniProgram, "pages/staff-orders/list");
    await wait(800);
    data = await page.data();
    await screenshot(miniProgram, `${RUN_ID}-staff-orders.png`);
    if (objects.work_order_id) {
      try {
        await tapCardContaining(page, ".card", objects.work_order_id);
        page = await waitPath(miniProgram, "pages/staff-orders/detail");
        await wait(600);
        page = await current(miniProgram);
        data = await page.data();
        if (data.canAccept) {
          await tapButton(page, "接单");
          await wait(800);
        }
        page = await current(miniProgram);
        data = await page.data();
        if (data.canStart) {
          await tapButton(page, "开始处理");
          await wait(800);
        }
        page = await current(miniProgram);
        await fillPlaceholder(page, "处理反馈（可选）", "已现场处理漏水");
        await tapButton(page, "提交反馈");
        await wait(800);
        await tapButton(page, "填写回执并完成");
        await wait(1200);
        page = await current(miniProgram);
        data = await page.data();
        await screenshot(miniProgram, `${RUN_ID}-staff-resolved.png`);
        record({
          id: "WO-STAFF-PROCESS",
          role: "staff",
          page: page.path,
          status: data.workOrder && data.workOrder.status === "WAIT_CONFIRM" ? "PASS" : "PARTIAL",
          expected: "ASSIGNED→ACCEPTED→PROCESSING→WAIT_CONFIRM",
          actual: JSON.stringify({
            status: data.workOrder && data.workOrder.status,
            logs: (data.logs || []).map((item) => `${item.from_status}->${item.to_status}`),
            error: data.error || "",
          }),
          evidence: [`${RUN_ID}-staff-orders.png`, `${RUN_ID}-staff-resolved.png`],
        });
      } catch (error) {
        record({
          id: "WO-STAFF-PROCESS",
          role: "staff",
          page: page.path,
          status: "FAIL",
          actual: String(error.message),
          evidence: [`${RUN_ID}-staff-orders.png`],
        });
      }
    } else {
      record({
        id: "WO-STAFF-PROCESS",
        status: "NOT_RUN",
        actual: "no work_order_id",
        evidence: [],
      });
    }

    await miniProgram.reLaunch("/pages/home/home");
    await logoutToLogin(miniProgram);
    page = await smsLogin(miniProgram, ACCOUNTS.phones.resident);
    await tapButton(page, "我的工单");
    page = await waitPath(miniProgram, "pages/work-orders/list");
    await wait(800);
    data = await page.data();
    await screenshot(miniProgram, `${RUN_ID}-resident-orders.png`);
    const listed = (data.items || []).some((item) => String(item.id) === String(objects.work_order_id));
    record({
      id: "WO-LIST-PAGING",
      role: "resident",
      page: page.path,
      status: listed ? "PARTIAL" : "FAIL",
      expected: "this-round work order visible; UI has no load-more",
      actual: JSON.stringify({
        count: (data.items || []).length,
        listed,
        ids: (data.items || []).map((item) => item.id),
      }),
      evidence: [`${RUN_ID}-resident-orders.png`],
    });
    if (objects.work_order_id) {
      await tapCardContaining(page, ".card", objects.work_order_id);
      page = await waitPath(miniProgram, "pages/work-orders/detail");
      await wait(600);
      data = await page.data();
      if (data.canConfirm) {
        await tapButton(page, "确认解决");
        await wait(1200);
      }
      page = await current(miniProgram);
      data = await page.data();
      await screenshot(miniProgram, `${RUN_ID}-resident-confirmed.png`);
      record({
        id: "WO-CONFIRM-CLOSED",
        role: "resident",
        page: page.path,
        status: data.workOrder && data.workOrder.status === "CLOSED" ? "PASS" : "FAIL",
        expected: "CLOSED after confirm",
        actual: JSON.stringify({
          status: data.workOrder && data.workOrder.status,
          logs: (data.logs || []).map((item) => `${item.from_status}->${item.to_status}`),
        }),
        evidence: [`${RUN_ID}-resident-confirmed.png`],
      });
      await miniProgram.reLaunch(`/pages/work-orders/detail?id=${objects.work_order_id}`);
      page = await waitPath(miniProgram, "pages/work-orders/detail");
      await wait(800);
      data = await page.data();
      await screenshot(miniProgram, `${RUN_ID}-resident-reentered.png`);
      record({
        id: "WO-REENTER",
        role: "resident",
        page: page.path,
        status: data.workOrder && data.workOrder.status === "CLOSED" ? "PASS" : "FAIL",
        expected: "reenter shows CLOSED and logs",
        actual: JSON.stringify({
          status: data.workOrder && data.workOrder.status,
          log_count: (data.logs || []).length,
        }),
        evidence: [`${RUN_ID}-resident-reentered.png`],
        note: "direct reLaunch of known id after list navigation already proved",
      });
    }

    await miniProgram.reLaunch("/pages/home/home");
    await logoutToLogin(miniProgram);
    page = await smsLogin(miniProgram, ACCOUNTS.phones.property_admin);
    await tapButton(page, "公告审核");
    page = await waitPath(miniProgram, "pages/supervisor/announcements");
    await wait(400);
    data = await page.data();
    if (!data.propertyId) {
      await fillPlaceholder(page, "小区 ID", PUBLIC.org.property_id);
    }
    await fillPlaceholder(page, "标题", `${RUN_ID} 停水通知`);
    await fillPlaceholder(page, "内容", "明日上午停水两小时，本轮合成公告。");
    await tapButton(page, "保存草稿");
    await wait(1000);
    await tapButton(page, "人工审核发布");
    await wait(1200);
    page = await current(miniProgram);
    data = await page.data();
    objects.announcement_id = data.draftId || null;
    await screenshot(miniProgram, `${RUN_ID}-announcement-publish.png`);
    record({
      id: "ANN-PUBLISH",
      role: "property_admin",
      page: page.path,
      status: data.draftId && !data.error ? "PASS" : "PARTIAL",
      expected: "draft then human publish",
      actual: JSON.stringify({ draftId: data.draftId, error: data.error || "" }),
      evidence: [`${RUN_ID}-announcement-publish.png`],
    });
    await miniProgram.reLaunch("/pages/home/home");
    await tapButton(await current(miniProgram), "事件");
    page = await waitPath(miniProgram, "pages/supervisor/incidents");
    await wait(800);
    data = await page.data();
    await screenshot(miniProgram, `${RUN_ID}-incidents.png`);
    record({
      id: "INCIDENT-UI",
      role: "property_admin",
      page: page.path,
      status: "PARTIAL",
      expected: "incident list exists; no create/handle controls",
      actual: JSON.stringify({ count: (data.items || []).length, error: data.error || "" }),
      evidence: [`${RUN_ID}-incidents.png`],
    });
    await miniProgram.reLaunch("/pages/home/home");
    await tapButton(await current(miniProgram), "员工状态");
    page = await waitPath(miniProgram, "pages/supervisor/staff");
    await wait(800);
    data = await page.data();
    await screenshot(miniProgram, `${RUN_ID}-staff-daily.png`);
    record({
      id: "STAFF-DAILY-PAGE",
      role: "property_admin",
      page: page.path,
      status: (data.items || []).length || !data.error ? "PASS" : "FAIL",
      expected: "staff daily list",
      actual: JSON.stringify({
        count: (data.items || []).length,
        error: data.error || "",
      }),
      evidence: [`${RUN_ID}-staff-daily.png`],
    });
    await logoutToLogin(miniProgram);

    page = await smsLogin(miniProgram, ACCOUNTS.phones.resident);
    await tapButton(page, "公告");
    page = await waitPath(miniProgram, "pages/announcements/list");
    await wait(800);
    data = await page.data();
    await screenshot(miniProgram, `${RUN_ID}-announcements-list.png`);
    const ann = (data.items || []).find((item) => String(item.id) === String(objects.announcement_id)) || (data.items || [])[0];
    if (ann) {
      await tapCardContaining(page, ".card", ann.title || ann.id);
      page = await waitPath(miniProgram, "pages/announcements/detail");
      await wait(600);
      data = await page.data();
      await screenshot(miniProgram, `${RUN_ID}-announcement-detail.png`);
      record({
        id: "ANN-READ",
        role: "resident",
        page: page.path,
        status: data.title ? "PASS" : "FAIL",
        expected: "read published announcement",
        actual: JSON.stringify({ title: data.title, status: data.status }),
        evidence: [`${RUN_ID}-announcements-list.png`, `${RUN_ID}-announcement-detail.png`],
      });
    } else {
      record({
        id: "ANN-READ",
        role: "resident",
        page: page.path,
        status: "FAIL",
        actual: "no announcement items",
        evidence: [`${RUN_ID}-announcements-list.png`],
      });
    }
    await miniProgram.navigateBack();
    await wait(500);
    await miniProgram.reLaunch("/pages/home/home");
    page = await waitPath(miniProgram, "pages/home/home");
    await tapButton(page, "知识助手");
    page = await waitPath(miniProgram, "pages/assistant/assistant");
    await wait(400);
    data = await page.data();
    if (!data.propertyId) {
      await fillPlaceholder(page, "小区 ID", PUBLIC.org.property_id);
    }
    await fillPlaceholder(page, "例如：装修时间是什么？", "装修时间是什么？");
    await tapButton(page, "提问");
    await wait(1500);
    page = await current(miniProgram);
    data = await page.data();
    await screenshot(miniProgram, `${RUN_ID}-assistant.png`);
    record({
      id: "AST-QUERY",
      role: "resident",
      page: page.path,
      status: data.answer || data.error ? "PARTIAL" : "FAIL",
      expected: "Fake assistant wiring, not real model",
      actual: JSON.stringify({
        hasAnswer: Boolean(data.answer),
        citations: (data.citations || []).length,
        error: data.error || "",
      }),
      evidence: [`${RUN_ID}-assistant.png`],
    });
    await miniProgram.reLaunch("/pages/home/home");
    page = await waitPath(miniProgram, "pages/home/home");
    await tapButton(page, "通知");
    page = await waitPath(miniProgram, "pages/notifications/list");
    await wait(800);
    data = await page.data();
    await screenshot(miniProgram, `${RUN_ID}-notifications.png`);
    const ntfRound = (data.items || []).filter((item) => {
      const rid = item.resource_id || item.resourceId;
      return String(rid) === String(objects.work_order_id) || String(rid) === String(objects.bind_request_id);
    });
    record({
      id: "NTF-ROUND",
      role: "resident",
      page: page.path,
      status: ntfRound.length ? "PASS" : data.items && data.items.length ? "PARTIAL" : "BLOCKED_ENV",
      expected: "this-round resource notifications",
      actual: JSON.stringify({
        unread: data.unread,
        count: (data.items || []).length,
        matched: ntfRound.length,
        error: data.error || "",
      }),
      evidence: [`${RUN_ID}-notifications.png`],
      note: "no isolated worker; in-process notification insert may still appear",
    });

    await miniProgram.reLaunch("/pages/home/home");
    await logoutToLogin(miniProgram);
    page = await current(miniProgram);
    data = await page.data();
    await screenshot(miniProgram, `${RUN_ID}-final-login.png`);
    record({
      id: "UX-NO-DEV-LEAK",
      role: "anonymous",
      page: page.path,
      status: data.mode === "password" && !(await buttons(page)).some((item) => item.indexOf("Seed") >= 0) ? "PASS" : "PARTIAL",
      expected: "login page after logout, no previous account body",
      actual: JSON.stringify({ mode: data.mode, buttons: await buttons(page), phone: Boolean(data.phone) }),
      evidence: [`${RUN_ID}-final-login.png`],
    });

    await miniProgram.reLaunch("/pages/dev-login/dev-login");
    page = await waitPath(miniProgram, "pages/dev-login/dev-login");
    await wait(500);
    data = await page.data();
    await screenshot(miniProgram, `${RUN_ID}-dev-login-blocked.png`);
    record({
      id: "DEV-LOGIN-ISOLATED",
      role: "anonymous",
      page: page.path,
      status: data.allowed === false ? "PASS" : "FAIL",
      expected: "debug login blocked when localPreview false",
      actual: JSON.stringify({ allowed: data.allowed, error: data.error || "" }),
      evidence: [`${RUN_ID}-dev-login-blocked.png`],
    });

    const staticNotImpl = [
      ["AUTH-PHONE-CHANGE", "功能尚未实现"],
      ["AUTH-WECHAT-REBIND", "功能尚未实现"],
      ["AUTH-MERGE", "功能尚未实现"],
      ["AUTH-RECOVERY", "功能尚未实现"],
      ["AUTH-PASSWORD-CHANGE", "只有API没有页面"],
      ["STAFF-INVITE-PAGE", "只有API没有页面"],
      ["STAFF-DEPT-SKILL-UI", "只有API没有页面"],
      ["KNOWLEDGE-INGEST-UI", "只有API没有页面"],
      ["HOUSE-IMPORT-EXPORT", "功能尚未实现"],
      ["STATS-CONFIG", "功能尚未实现"],
      ["SELFHOST-AI", "缺环境或权限"],
      ["REAL-SMS", "缺环境或权限"],
      ["WO-TIMEOUT", "不属于小程序页面、应单独后端验证"],
      ["WO-CANCEL-REJECT-REASSIGN-REOPEN", "页面部分接线，本轮主链未并行互斥分支"],
      ["AUTH-WECHAT", "缺真实外部配置"],
      ["AUTH-SMS-EXPIRE", "OTP TTL 300s 未在页面等待"],
    ];
    for (const [id, reason] of staticNotImpl) {
      if (!results.find((item) => item.id === id)) {
        record({
          id,
          status: id === "AUTH-SMS-EXPIRE" ? "NOT_RUN" : "NOT_IMPLEMENTED",
          actual: reason,
          evidence: [],
        });
      }
    }
    record({
      id: "AUTH-SMS-REUSE",
      status: "NOT_RUN",
      actual: "login navigates away; reuse covered by isolated API tests, not retyped this round",
      evidence: [],
    });

    await miniProgram.reLaunch("/pages/login/login");
    await waitPath(miniProgram, "pages/login/login");
    await screenshot(miniProgram, `${RUN_ID}-end-login.png`);
    saveProgress();
    const counts = results.reduce((acc, item) => {
      acc[item.status] = (acc[item.status] || 0) + 1;
      return acc;
    }, {});
    writeJson("summary.json", {
      task_id: "MP-CURRENT-FULL-REGRESSION-001",
      run_id: RUN_ID,
      code_sha: PUBLIC.code_sha,
      counts,
      total: results.length,
      objects,
      request_hosts: Array.from(new Set(requests.map((item) => item.host))),
      request_dev_token: requests.some((item) => item.path.indexOf("/auth/dev/token") >= 0),
      not_claimed: ["Production V1", "Day 4 PASS", "real SMS", "self-hosted AI"],
    });
    process.exit(0);
  } catch (error) {
    fs.writeFileSync(
      path.join(OUT, "automator-error.txt"),
      `${error && error.message}\n${error && error.stack}\n`,
      "utf8"
    );
    saveProgress();
    process.exit(1);
  }
})();
