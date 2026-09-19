"use strict";

const fs = require("fs");
const http = require("http");
const path = require("path");
const automator = require(process.env.MP_AUTOMATOR_MODULE);

const OUT = path.resolve(__dirname);
const ACCOUNTS_PATH = path.join(OUT, "accounts.local.json");
const ACCOUNTS = JSON.parse(fs.readFileSync(ACCOUNTS_PATH, "utf8"));
const RUN_ID = "mp-cfull-20260919-fix1";
const WS = process.env.MP_AUTOMATOR_WS || "ws://127.0.0.1:9434";
const startedAt = new Date().toISOString();
const payload = {
  task_id: "MP-CURRENT-FULL-REGRESSION-001-FIX-1",
  run_id: RUN_ID,
  related: "AUTH-IDENTITY-AI-001-C",
  started_at: startedAt,
  code_sha_product: "ab0b8822b34ab26c4c2960dde00c157c3b141725",
  code_sha_reports: null,
  api: "http://127.0.0.1:8083/api/v1",
  revision: "20260919_0009",
  provider_mode: "password=real; sms=development inbox; ai=Fake",
  automator_ws: WS,
  results: [],
  objects: {},
};

function log(msg) {
  fs.appendFileSync(path.join(OUT, "run-log.txt"), `${new Date().toISOString()} ${msg}\n`, "utf8");
  console.log(msg);
}

function wait(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function save() {
  fs.writeFileSync(path.join(OUT, "fix1-results.json"), JSON.stringify(payload, null, 2), "utf8");
}

function record(item) {
  item.run_id = RUN_ID;
  item.code_sha_product = payload.code_sha_product;
  item.api = payload.api;
  item.revision = payload.revision;
  item.provider_mode = payload.provider_mode;
  payload.results.push(item);
  save();
  log(`${item.id} ${item.status}`);
}

function requestJson(method, pathname, body, headers) {
  return new Promise((resolve, reject) => {
    const data = body === undefined ? "" : JSON.stringify(body);
    const req = http.request(
      {
        host: "127.0.0.1",
        port: 8083,
        path: `/api/v1${pathname}`,
        method,
        headers: {
          "Content-Type": "application/json",
          "X-Request-Id": `${RUN_ID}-sidecar`,
          ...(headers || {}),
          ...(data ? { "Content-Length": Buffer.byteLength(data) } : {}),
        },
      },
      (res) => {
        let raw = "";
        res.on("data", (chunk) => {
          raw += chunk;
        });
        res.on("end", () => {
          let json = {};
          try {
            json = raw ? JSON.parse(raw) : {};
          } catch (error) {
            json = {};
          }
          resolve({ status: res.statusCode, json });
        });
      }
    );
    req.on("error", reject);
    if (data) req.write(data);
    req.end();
  });
}

function answer(prompt) {
  const match = String(prompt || "")
    .trim()
    .match(/^(\d+)\s*\+\s*(\d+)\s*=\s*\?$/);
  if (!match) throw new Error("bad prompt");
  return String(Number(match[1]) + Number(match[2]));
}

async function inbox(phone, purpose) {
  for (let i = 0; i < 12; i += 1) {
    const payloadInbox = await requestJson("POST", "/auth/sms/test-inbox", { phone, purpose });
    const code = payloadInbox.json && payloadInbox.json.data && payloadInbox.json.data.code;
    if (code) return code;
    await wait(400);
  }
  throw new Error(`inbox empty ${purpose}`);
}

async function shot(mp, name) {
  await mp.screenshot({ path: path.join(OUT, name) });
}

async function current(mp) {
  return mp.currentPage();
}

async function waitPath(mp, frag, tries) {
  let page;
  for (let i = 0; i < (tries || 40); i += 1) {
    page = await current(mp);
    if (page && page.path && page.path.indexOf(frag) >= 0) return page;
    await wait(400);
  }
  throw new Error(`no ${frag} last ${page && page.path}`);
}

async function waitHomeReady(mp) {
  for (let i = 0; i < 40; i += 1) {
    const page = await waitPath(mp, "pages/home/home");
    const data = await page.data();
    if (data && data.me) return page;
    await wait(400);
  }
  throw new Error("home me missing");
}

async function tap(page, label) {
  const nodes = await page.$$("button");
  const labels = [];
  for (const node of nodes) {
    const text = ((await node.text()) || "").trim();
    labels.push(text);
    if (text === label) {
      await node.tap();
      return;
    }
  }
  throw new Error(`no button ${label}; have ${labels.join("|")}`);
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
  throw new Error(`no ${selector} ${expected}`);
}

async function fill(page, placeholder, value) {
  const els = [...(await page.$$("input")), ...(await page.$$("textarea"))];
  for (const el of els) {
    if ((await el.attribute("placeholder")) === placeholder) {
      await el.tap();
      await wait(120);
      await el.input(String(value));
      return;
    }
  }
  throw new Error(`no input ${placeholder}`);
}

async function relaunchLogin(mp) {
  const page = await current(mp);
  if (page && page.path && page.path.indexOf("pages/home/home") >= 0) {
    try {
      await tap(page, "退出登录");
      await wait(1000);
    } catch (error) {
      log(`logout-before-login ${error.message}`);
    }
  }
  const now = await current(mp);
  if (now && now.path && now.path.indexOf("pages/login/login") >= 0) return now;
  await Promise.race([
    mp.evaluate(() => {
      try {
        wx.clearStorageSync();
      } catch (error) {
        // ignore local cache clear failure
      }
      wx.reLaunch({ url: "/pages/login/login" });
    }),
    wait(8000),
  ]);
  await wait(800);
  return waitPath(mp, "pages/login/login");
}

async function logout(mp) {
  const page = await current(mp);
  if (page && page.path && page.path.indexOf("pages/home/home") >= 0) {
    try {
      await tap(page, "退出登录");
      await wait(1000);
    } catch (error) {
      log(`logout tap failed ${error.message}`);
    }
  }
  return relaunchLogin(mp);
}

async function ensureSms(page) {
  const data = await page.data();
  if (data.mode === "sms") return page;
  await tapContains(page, ".link-text", "验证码登录");
  await wait(400);
  return page;
}

async function smsLogin(mp, phone) {
  let page = await waitPath(mp, "pages/login/login");
  page = await ensureSms(page);
  page = await current(mp);
  await fill(page, "手机号", phone);
  await tap(page, "获取验证码");
  await wait(900);
  page = await current(mp);
  let data = await page.data();
  if (!data.challengePrompt) throw new Error("no challenge");
  await fill(page, "挑战答案", answer(data.challengePrompt));
  await tap(page, "获取验证码");
  await wait(900);
  const code = await inbox(phone, "login");
  page = await current(mp);
  await fill(page, "短信验证码", code);
  await tap(page, "登录");
  return waitHomeReady(mp);
}

async function passwordLogin(mp, phone, password) {
  let page = await waitPath(mp, "pages/login/login");
  const data = await page.data();
  if (data.mode === "sms") {
    await tapContains(page, ".link-text", "密码登录");
    await wait(400);
    page = await current(mp);
  }
  await fill(page, "手机号", phone);
  await fill(page, "密码", password);
  await tap(page, "登录");
  return waitHomeReady(mp);
}

function persistPassword(nextPassword) {
  const next = JSON.parse(fs.readFileSync(ACCOUNTS_PATH, "utf8"));
  next.password = nextPassword;
  fs.writeFileSync(ACCOUNTS_PATH, `${JSON.stringify(next, null, 2)}\n`, "utf8");
}

(async () => {
  log(`fix1 start ws=${WS}`);
  save();
  const mp = await automator.connect({ wsEndpoint: WS });
  log("connected");
  await relaunchLogin(mp);

  const residentPhone = ACCOUNTS.phones.resident;
  const oldPassword = ACCOUNTS.password;
  const newPassword = "resetpass9";

  await passwordLogin(mp, residentPhone, oldPassword);
  log("resident password home");
  const tokens = await mp.evaluate(() => ({
    access: wx.getStorageSync("access_token") || "",
    refresh: wx.getStorageSync("refresh_token") || "",
  }));
  const hadTokens = Boolean(tokens && tokens.access && tokens.refresh);
  await relaunchLogin(mp);
  let page = await waitPath(mp, "pages/login/login");
  await fill(page, "手机号", residentPhone);
  await tapContains(page, ".link-text", "忘记密码");
  page = await waitPath(mp, "pages/password-reset/password-reset");
  await fill(page, "手机号", residentPhone);
  await tap(page, "获取验证码");
  await wait(900);
  page = await current(mp);
  let data = await page.data();
  if (!data.challengePrompt) throw new Error("reset no challenge");
  await fill(page, "挑战答案", answer(data.challengePrompt));
  await tap(page, "获取验证码");
  await wait(900);
  const resetCode = await inbox(residentPhone, "reset");
  page = await current(mp);
  await fill(page, "短信验证码", resetCode);
  await tap(page, "验证短信");
  await wait(1200);
  page = await waitPath(mp, "pages/password-reset/password-reset");
  data = await page.data();
  if (data.step !== 2) throw new Error(`reset step ${data.step}`);
  await fill(page, "新密码", newPassword);
  await fill(page, "确认新密码", newPassword);
  await tap(page, "确认重置");
  page = await waitPath(mp, "pages/login/login", 50);
  data = await page.data();
  await shot(mp, `${RUN_ID}-reset-back-login.png`);
  persistPassword(newPassword);
  const loginVisible = Boolean(data && data.mode);
  record({
    id: "AUTH-RESET",
    role: "resident",
    page: page.path,
    status: page.path.indexOf("pages/login/login") >= 0 && loginVisible && data.mode === "password" ? "PASS" : "PARTIAL",
    expected: "return to login, not auto logged in",
    actual: `path=${page.path} mode=${data.mode || ""}`,
    evidence: [`${RUN_ID}-reset-back-login.png`],
    note: "FIX-1 unique screenshot after waitPath login; password value not recorded",
  });

  let meOld = { status: 0 };
  let refreshOld = { status: 0 };
  if (hadTokens) {
    meOld = await requestJson("GET", "/me", undefined, {
      Authorization: `Bearer ${tokens.access}`,
    });
    refreshOld = await requestJson("POST", "/auth/token/refresh", {
      refresh_token: tokens.refresh,
    });
  }
  page = await current(mp);
  if ((await page.data()).mode === "sms") {
    await tapContains(page, ".link-text", "密码登录");
    await wait(400);
    page = await current(mp);
  }
  await fill(page, "手机号", residentPhone);
  await fill(page, "密码", oldPassword);
  await tap(page, "登录");
  await wait(1200);
  page = await current(mp);
  const stale = await page.data();
  await shot(mp, `${RUN_ID}-reset-old-password.png`);
  const staleFailed = page.path.indexOf("login") >= 0 && Boolean(stale.error);
  const tokenRevoked = hadTokens && meOld.status === 401 && refreshOld.status === 401;
  record({
    id: "AUTH-RESET-REVOKE",
    role: "resident",
    page: "pages/login/login",
    status: tokenRevoked && staleFailed ? "PASS" : "PARTIAL",
    expected: "old access/refresh 401; old password stays on login with error",
    actual: `hadTokens=${hadTokens} me=${meOld.status} refresh=${refreshOld.status} stale_error=${Boolean(stale.error)} path=${page.path}`,
    evidence: [`${RUN_ID}-reset-old-password.png`, `${RUN_ID}-reset-back-login.png`],
    note: "UI old-password plus sidecar /me and /auth/token/refresh; tokens not written to reports",
  });
  await fill(page, "密码", newPassword);
  await tap(page, "登录");
  page = await waitHomeReady(mp);
  await shot(mp, `${RUN_ID}-reset-relogin-home.png`);
  record({
    id: "AUTH-RESET-RELOGIN",
    role: "resident",
    page: page.path,
    status: page.path.indexOf("pages/home/home") >= 0 ? "PASS" : "FAIL",
    expected: "new password enters home",
    actual: `path=${page.path}`,
    evidence: [`${RUN_ID}-reset-relogin-home.png`],
  });

  await logout(mp);
  await smsLogin(mp, ACCOUNTS.phones.staff2);
  page = await waitHomeReady(mp);
  await tap(page, "账号与安全");
  page = await waitPath(mp, "pages/profile/profile");
  await tap(page, "员工邀请认领");
  page = await waitPath(mp, "pages/employee-claim/employee-claim");
  await fill(page, "邀请令牌", ACCOUNTS.invite_token);
  await tap(page, "认领");
  await wait(1500);
  page = await current(mp);
  data = await page.data();
  await shot(mp, `${RUN_ID}-staff2-dup-claim.png`);
  const onClaim = page.path.indexOf("employee-claim") >= 0;
  record({
    id: "STAFF-CLAIM-DUP",
    role: "staff2",
    page: page.path,
    status: onClaim && Boolean(data.error) ? "PASS" : "FAIL",
    expected: "stable error on claim page",
    actual: `path=${page.path} error=${Boolean(data.error)}`,
    evidence: [`${RUN_ID}-staff2-dup-claim.png`],
    note: "navigated home → 账号与安全 → 员工邀请认领",
  });

  await logout(mp);
  await smsLogin(mp, ACCOUNTS.phones.unbound);
  page = await waitHomeReady(mp);
  await tap(page, "退出登录");
  page = await waitPath(mp, "pages/login/login", 50);
  page = await ensureSms(page);
  page = await current(mp);
  await fill(page, "手机号", ACCOUNTS.phones.unbound);
  await tap(page, "获取验证码");
  await wait(900);
  page = await current(mp);
  data = await page.data();
  await fill(page, "挑战答案", answer(data.challengePrompt));
  await tap(page, "获取验证码");
  await wait(900);
  const reuseCode = await inbox(ACCOUNTS.phones.unbound, "login");
  page = await current(mp);
  await fill(page, "短信验证码", reuseCode);
  await tap(page, "登录");
  await waitHomeReady(mp);
  await tap(await current(mp), "退出登录");
  page = await waitPath(mp, "pages/login/login", 50);
  page = await ensureSms(page);
  page = await current(mp);
  await fill(page, "手机号", ACCOUNTS.phones.unbound);
  await fill(page, "短信验证码", reuseCode);
  await tap(page, "登录");
  await wait(1200);
  page = await current(mp);
  data = await page.data();
  await shot(mp, `${RUN_ID}-sms-reuse.png`);
  record({
    id: "AUTH-SMS-REUSE",
    role: "unbound",
    page: page.path,
    status: page.path.indexOf("login") >= 0 && Boolean(data.error) ? "PASS" : "FAIL",
    expected: "consumed OTP rejected on login page",
    actual: `path=${page.path} error=${Boolean(data.error)}`,
    evidence: [`${RUN_ID}-sms-reuse.png`],
  });

  record({
    id: "AUTH-SMS-EXPIRE",
    role: "anonymous",
    page: "pages/login/login",
    status: "PARTIAL",
    expected: "expired OTP rejected",
    actual: "page wait 300s not executed; prior isolated 0009 API expire remains the software evidence",
    evidence: [],
    note: "UI TTL wait skipped; not claimed as page PASS",
  });

  await logout(mp);
  await smsLogin(mp, ACCOUNTS.phones.property_admin);
  page = await waitHomeReady(mp);
  await tap(page, "事件");
  page = await waitPath(mp, "pages/supervisor/incidents");
  await wait(1200);
  data = await page.data();
  await shot(mp, `${RUN_ID}-incidents.png`);
  record({
    id: "INCIDENT-UI",
    role: "property_admin",
    page: page.path,
    status: "PARTIAL",
    expected: "list page reachable from 物业 home 事件",
    actual: `path=${page.path} items=${(data.items || []).length} propertyId=${data.propertyId || ""} error=${data.error || ""}`,
    evidence: [`${RUN_ID}-incidents.png`],
    note: "list/empty only; no create/aggregate UI; raw propertyId control remains V1 UX gap",
  });
  page = await current(mp);
  if (page.path.indexOf("incidents") >= 0) {
    await mp.evaluate(() => wx.navigateBack({ delta: 1 }));
    await wait(800);
  }
  page = await waitHomeReady(mp);
  await tap(page, "员工状态");
  page = await waitPath(mp, "pages/supervisor/staff");
  await wait(1200);
  data = await page.data();
  await shot(mp, `${RUN_ID}-staff-daily.png`);
  record({
    id: "STAFF-DAILY-PAGE",
    role: "property_admin",
    page: page.path,
    status: page.path.indexOf("supervisor/staff") >= 0 ? "PASS" : "FAIL",
    expected: "员工状态 page from home",
    actual: `path=${page.path} items=${(data.items || []).length} propertyId=${data.propertyId || ""}`,
    evidence: [`${RUN_ID}-staff-daily.png`],
  });

  await logout(mp);
  await shot(mp, `${RUN_ID}-end-login.png`);
  payload.ended_at = new Date().toISOString();
  save();
  log("fix1 done");
  process.exit(0);
})().catch((error) => {
  payload.ended_at = new Date().toISOString();
  payload.fatal = error && error.message;
  save();
  fs.writeFileSync(
    path.join(OUT, "fix1-error.txt"),
    `${error && error.message}\n${error && error.stack}\n`,
    "utf8"
  );
  log(`fix1 fail ${error && error.message}`);
  process.exit(1);
});
