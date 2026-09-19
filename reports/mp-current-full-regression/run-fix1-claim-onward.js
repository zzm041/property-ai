"use strict";

const fs = require("fs");
const http = require("http");
const path = require("path");
const automator = require(process.env.MP_AUTOMATOR_MODULE);

const OUT = path.resolve(__dirname);
const ACCOUNTS = JSON.parse(fs.readFileSync(path.join(OUT, "accounts.local.json"), "utf8"));
const RUN_ID = "mp-cfull-20260919-fix1";
const WS = process.env.MP_AUTOMATOR_WS || "ws://127.0.0.1:9435";
const payload = JSON.parse(fs.readFileSync(path.join(OUT, "fix1-results.json"), "utf8"));
payload.fatal = undefined;
payload.api_restarted_after_claim_400 = true;
payload.from_claim_at = new Date().toISOString();

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
  const index = payload.results.findIndex((row) => row.id === item.id);
  if (index >= 0) payload.results[index] = item;
  else payload.results.push(item);
  save();
  log(`${item.id} ${item.status}`);
}

function requestJson(method, pathname, body) {
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
  await wait(700);
  try {
    await mp.screenshot({ path: path.join(OUT, name) });
    log(`shot ${name}`);
  } catch (error) {
    log(`shot fail ${name} ${error.message}`);
  }
}

async function current(mp) {
  return mp.currentPage();
}

async function waitPath(mp, frag, tries) {
  let page;
  for (let i = 0; i < (tries || 25); i += 1) {
    page = await current(mp);
    if (page && page.path && page.path.indexOf(frag) >= 0) return page;
    await wait(400);
  }
  throw new Error(`no ${frag} last ${page && page.path}`);
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
      await wait(100);
      await el.input(String(value));
      return;
    }
  }
  throw new Error(`no input ${placeholder}`);
}

async function smsLogin(mp, phone) {
  log(`sms last4=${phone.slice(-4)}`);
  let page = await waitPath(mp, "pages/login/login");
  let data = await page.data();
  if (data.mode !== "sms") {
    await tapContains(page, ".link-text", "验证码登录");
    await wait(400);
    page = await current(mp);
  }
  await fill(page, "手机号", phone);
  await tap(page, "获取验证码");
  await wait(900);
  page = await current(mp);
  data = await page.data();
  if (!data.challengePrompt) throw new Error("no challenge");
  await fill(page, "挑战答案", answer(data.challengePrompt));
  await tap(page, "获取验证码");
  await wait(900);
  const code = await inbox(phone, "login");
  page = await current(mp);
  await fill(page, "短信验证码", code);
  await tap(page, "登录");
  page = await waitPath(mp, "pages/home/home", 40);
  await wait(900);
  return page;
}

(async () => {
  const mp = await automator.connect({ wsEndpoint: WS });
  log(`claim-onward connected ${WS}`);
  let page = await current(mp);
  log(`start ${page.path}`);
  if (page.path.indexOf("login") < 0) {
    if (page.path.indexOf("home") >= 0) {
      await tap(page, "退出登录");
      await waitPath(mp, "pages/login/login", 40);
    } else {
      throw new Error(`unexpected ${page.path}`);
    }
  }

  page = await smsLogin(mp, ACCOUNTS.phones.staff2);
  log("staff2 home");
  page = await current(mp);
  await tap(page, "账号与安全");
  page = await waitPath(mp, "pages/profile/profile");
  await tap(page, "员工邀请认领");
  page = await waitPath(mp, "pages/employee-claim/employee-claim");
  log("on claim");
  await fill(page, "邀请令牌", ACCOUNTS.invite_token);
  await tap(page, "认领");
  await wait(1800);
  page = await current(mp);
  let data = await page.data();
  await shot(mp, `${RUN_ID}-staff2-dup-claim.png`);
  record({
    id: "STAFF-CLAIM-DUP",
    role: "staff2",
    page: page.path,
    status: page.path.indexOf("employee-claim") >= 0 && Boolean(data.error) ? "PASS" : "FAIL",
    expected: "stable error on claim page",
    actual: `path=${page.path} error=${Boolean(data.error)}`,
    evidence: [`${RUN_ID}-staff2-dup-claim.png`],
    note: "After invalid-invite 400 fix; navigated home → 账号与安全 → 员工邀请认领",
    baseline_status: "FAIL on ab0b882 because invalid invite returned 401 and client reLaunched login",
  });

  if (page.path.indexOf("employee-claim") >= 0) {
    try {
      await tap(page, "返回");
    } catch (error) {
      await mp.navigateBack();
    }
    await wait(800);
    page = await current(mp);
    if (page.path.indexOf("profile") >= 0) {
      await tap(page, "返回");
      await wait(800);
    }
  }
  page = await waitPath(mp, "pages/home/home");
  page = await current(mp);
  await tap(page, "退出登录");
  await waitPath(mp, "pages/login/login", 40);

  await smsLogin(mp, ACCOUNTS.phones.unbound);
  page = await current(mp);
  await tap(page, "退出登录");
  page = await waitPath(mp, "pages/login/login", 40);
  page = await current(mp);
  data = await page.data();
  if (data.mode !== "sms") {
    await tapContains(page, ".link-text", "验证码登录");
    await wait(400);
    page = await current(mp);
  }
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
  await waitPath(mp, "pages/home/home", 40);
  await wait(700);
  page = await current(mp);
  await tap(page, "退出登录");
  page = await waitPath(mp, "pages/login/login", 40);
  page = await current(mp);
  data = await page.data();
  if (data.mode !== "sms") {
    await tapContains(page, ".link-text", "验证码登录");
    await wait(400);
    page = await current(mp);
  }
  await fill(page, "手机号", ACCOUNTS.phones.unbound);
  await fill(page, "短信验证码", reuseCode);
  await tap(page, "登录");
  await wait(1500);
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

  page = await current(mp);
  if (page.path.indexOf("login") < 0) {
    page = await waitPath(mp, "pages/home/home");
    await tap(page, "退出登录");
    await waitPath(mp, "pages/login/login", 40);
  }
  await smsLogin(mp, ACCOUNTS.phones.property_admin);
  page = await current(mp);
  await tap(page, "事件");
  page = await waitPath(mp, "pages/supervisor/incidents");
  await wait(1000);
  page = await current(mp);
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
  await mp.navigateBack();
  page = await waitPath(mp, "pages/home/home");
  await wait(500);
  page = await current(mp);
  await tap(page, "员工状态");
  page = await waitPath(mp, "pages/supervisor/staff");
  await wait(1000);
  page = await current(mp);
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
  await mp.navigateBack();
  page = await waitPath(mp, "pages/home/home");
  await wait(400);
  page = await current(mp);
  await tap(page, "退出登录");
  await waitPath(mp, "pages/login/login", 40);
  await shot(mp, `${RUN_ID}-end-login.png`);
  payload.ended_at = new Date().toISOString();
  save();
  log("claim-onward done");
  process.exit(0);
})().catch((error) => {
  payload.ended_at = new Date().toISOString();
  payload.fatal = error && error.message;
  save();
  fs.writeFileSync(path.join(OUT, "fix1-error.txt"), `${error && error.message}\n${error && error.stack}\n`, "utf8");
  log(`claim-onward fail ${error && error.message}`);
  process.exit(1);
});
