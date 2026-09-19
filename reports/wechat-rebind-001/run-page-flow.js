"use strict";

const fs = require("fs");
const http = require("http");
const path = require("path");
const automator = require(process.env.MP_AUTOMATOR_MODULE);

const OUT = __dirname;
const API_PREFIX = "/api/v1";
const WS = process.env.MP_AUTOMATOR_WS || "ws://127.0.0.1:9435";
const evidence = {
  task_id: "WECHAT-REBIND-001",
  environment: {
    api: "http://127.0.0.1:8083/api/v1",
    database: "127.0.0.1:3307/property_ai_auth001_test",
    migration: "20260919_0009",
    wechat_provider: "development",
    sms_provider: "development",
    interaction: "WeChat DevTools automator page controls",
  },
  checks: {},
  screenshots: [],
};
let activeMiniProgram = null;

function wait(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function redact(text) {
  return String(text || "")
    .replace(/\b1[3-9]\d{9}\b/g, "[REDACTED_PHONE]")
    .replace(/Bearer\s+\S+/gi, "Bearer [REDACTED]")
    .replace(/(?:access|refresh)[_-]?token[^\s]*/gi, "[REDACTED_TOKEN_FIELD]");
}

function save() {
  fs.writeFileSync(
    path.join(OUT, "page-evidence.json"),
    `${JSON.stringify(evidence, null, 2)}\n`,
    "utf8"
  );
}

function maskPhone(phone) {
  return `${phone.slice(0, 3)}****${phone.slice(-4)}`;
}

function requestJson(method, pathname, body, accessToken) {
  return new Promise((resolve, reject) => {
    const encoded = body === undefined ? null : JSON.stringify(body);
    const headers = { "X-Request-Id": "wechat-rebind-001-page-flow" };
    if (encoded !== null) {
      headers["Content-Type"] = "application/json";
      headers["Content-Length"] = Buffer.byteLength(encoded);
    }
    if (accessToken) {
      headers.Authorization = `Bearer ${accessToken}`;
    }
    const request = http.request(
      {
        host: "127.0.0.1",
        port: 8083,
        path: `${API_PREFIX}${pathname}`,
        method,
        headers,
      },
      (response) => {
        let raw = "";
        response.on("data", (chunk) => {
          raw += chunk;
        });
        response.on("end", () => {
          let payload = {};
          try {
            payload = raw ? JSON.parse(raw) : {};
          } catch (error) {
            reject(new Error(`Invalid JSON from ${pathname}`));
            return;
          }
          resolve({
            status: response.statusCode,
            data: payload.data,
            errorCode: payload.error && payload.error.code,
          });
        });
      }
    );
    request.on("error", reject);
    if (encoded !== null) {
      request.write(encoded);
    }
    request.end();
  });
}

function challengeAnswer(prompt) {
  const match = String(prompt || "").match(/^(\d+)\s*\+\s*(\d+)\s*=\s*\?$/);
  if (!match) {
    throw new Error("Unsupported visual challenge prompt");
  }
  return String(Number(match[1]) + Number(match[2]));
}

async function current(miniProgram) {
  return miniProgram.currentPage();
}

async function waitPath(miniProgram, fragment, attempts = 50) {
  const expected = String(fragment).replace(/^\//, "");
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    const page = await current(miniProgram);
    if (page && page.path && page.path.includes(expected)) {
      return page;
    }
    await wait(300);
  }
  const page = await current(miniProgram);
  throw new Error(`Expected ${expected}; current=${page && page.path}`);
}

async function waitData(page, predicate, description, attempts = 50) {
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    const data = await page.data();
    if (predicate(data)) {
      return data;
    }
    if (data.error) {
      throw new Error(`${description}: ${data.error}`);
    }
    await wait(300);
  }
  throw new Error(`${description} timed out`);
}

async function tap(page, label) {
  for (const node of await page.$$("button")) {
    if (((await node.text()) || "").trim() === label) {
      await node.tap();
      return;
    }
  }
  throw new Error(`Button not found: ${label}`);
}

async function tapLink(page, label) {
  for (const node of await page.$$(".link-text")) {
    if (((await node.text()) || "").trim() === label) {
      await node.tap();
      return;
    }
  }
  throw new Error(`Link not found: ${label}`);
}

async function fill(page, placeholder, value) {
  for (const field of await page.$$("input")) {
    if ((await field.attribute("placeholder")) === placeholder) {
      await field.input(String(value));
      return;
    }
  }
  throw new Error(`Field not found: ${placeholder}`);
}

async function resetToLogin(miniProgram) {
  await miniProgram.callWxMethod("clearStorageSync");
  await miniProgram.callWxMethod("reLaunch", { url: "/pages/login/login" });
  return waitPath(miniProgram, "pages/login/login");
}

async function inbox(phone, purpose) {
  const response = await requestJson("POST", "/auth/sms/test-inbox", {
    phone,
    purpose,
  });
  if (response.status !== 200 || !response.data || !response.data.code) {
    throw new Error(`Development SMS inbox failed with ${response.status}`);
  }
  return response.data.code;
}

async function smsLogin(miniProgram, phone) {
  let page = await waitPath(miniProgram, "pages/login/login");
  if ((await page.data()).mode !== "sms") {
    await tapLink(page, "验证码登录");
    await waitData(page, (data) => data.mode === "sms", "SMS login mode");
  }
  page = await current(miniProgram);
  await fill(page, "手机号", phone);
  await tap(page, "获取验证码");
  const challenge = await waitData(
    page,
    (data) => Boolean(data.challengePrompt),
    "login visual challenge"
  );
  await fill(page, "挑战答案", challengeAnswer(challenge.challengePrompt));
  await tap(page, "获取验证码");
  await wait(500);
  await fill(page, "短信验证码", await inbox(phone, "login"));
  await tap(page, "登录");
  return waitPath(miniProgram, "pages/home/home");
}

async function requestWechatVerificationCode(page, phone) {
  await tap(page, "获取验证短信");
  const challenge = await waitData(
    page,
    (data) => Boolean(data.wechatChallengePrompt) && !data.loading,
    "WeChat rebind visual challenge"
  );
  await fill(page, "挑战答案", challengeAnswer(challenge.wechatChallengePrompt));
  await tap(page, "获取验证短信");
  await waitData(
    page,
    (data) =>
      !data.loading &&
      String(data.wechatNotice || "").includes("当前手机号验证码已发送"),
    "WeChat rebind SMS send"
  );
  return inbox(phone, "login");
}

(async () => {
  save();
  const suffix = String(
    (Date.now() + Math.floor(Math.random() * 10000)) % 100000000
  ).padStart(8, "0");
  const phone = `185${suffix}`;
  const miniProgram = await automator.connect({ wsEndpoint: WS });
  activeMiniProgram = miniProgram;

  await resetToLogin(miniProgram);
  let page = await smsLogin(miniProgram, phone);
  const initialTokens = await miniProgram.evaluate(() => ({
    access: wx.getStorageSync("access_token") || "",
    refresh: wx.getStorageSync("refresh_token") || "",
  }));
  const initialMe = await requestJson("GET", "/me", undefined, initialTokens.access);
  if (initialMe.status !== 200 || !initialMe.data || !initialMe.data.user_id) {
    throw new Error("SMS page login did not establish a user identity");
  }

  await tap(page, "账号与安全");
  page = await waitPath(miniProgram, "pages/profile/profile");
  await tap(page, "绑定当前微信");
  await waitData(
    page,
    (data) => !data.loading && data.wechatNotice === "当前微信已绑定",
    "initial WeChat page bind"
  );

  await fill(page, "当前账号手机号", phone);
  await fill(
    page,
    "当前手机号验证码",
    await requestWechatVerificationCode(page, phone)
  );
  await tap(page, "确认更换为当前微信");
  const successData = await waitData(
    page,
    (data) =>
      !data.loading &&
      data.wechatNotice === "微信已安全更换，其他旧会话已失效",
    "verified WeChat page rebind"
  );
  const replacementTokens = await miniProgram.evaluate(() => ({
    access: wx.getStorageSync("access_token") || "",
    refresh: wx.getStorageSync("refresh_token") || "",
  }));
  const oldAccess = await requestJson("GET", "/me", undefined, initialTokens.access);
  const oldRefresh = await requestJson("POST", "/auth/token/refresh", {
    refresh_token: initialTokens.refresh,
  });
  const replacementMe = await requestJson(
    "GET",
    "/me",
    undefined,
    replacementTokens.access
  );
  if (
    oldAccess.status !== 401 ||
    oldRefresh.status !== 401 ||
    replacementMe.status !== 200 ||
    replacementMe.data.user_id !== initialMe.data.user_id
  ) {
    throw new Error("Rebind session rotation or identity continuity failed");
  }

  const screenshot = "01-wechat-rebind-success.png";
  await miniProgram.screenshot({ path: path.join(OUT, screenshot) });
  evidence.screenshots.push(screenshot);
  evidence.checks = {
    initial_sms_login: {
      status: "PASS",
      user_id: initialMe.data.user_id,
      phone_masked: maskPhone(phone),
      page: "pages/home/home",
    },
    initial_wechat_bind: {
      status: "PASS",
      page_notice: "当前微信已绑定",
    },
    verified_rebind: {
      status: "PASS",
      page_notice: successData.wechatNotice,
      sensitive_fields_cleared_before_screenshot:
        !successData.wechatPhone &&
        !successData.wechatCode &&
        !successData.wechatChallengeAnswer,
      replacement_access_changed:
        replacementTokens.access !== initialTokens.access,
      replacement_refresh_changed:
        replacementTokens.refresh !== initialTokens.refresh,
    },
    session_rotation: {
      status: "PASS",
      old_access_status: oldAccess.status,
      old_refresh_status: oldRefresh.status,
      replacement_access_status: replacementMe.status,
      same_user_id: replacementMe.data.user_id === initialMe.data.user_id,
    },
  };
  evidence.status = "PASS";
  save();
  await miniProgram.disconnect();
  activeMiniProgram = null;
})().catch(async (error) => {
  evidence.status = "FAIL";
  evidence.error = redact(error && (error.stack || error.message || error));
  save();
  fs.writeFileSync(
    path.join(OUT, "page-flow-error.txt"),
    `${evidence.error}\n`,
    "utf8"
  );
  if (activeMiniProgram) {
    try {
      await activeMiniProgram.disconnect();
    } catch (disconnectError) {
      // Preserve the original failure.
    }
  }
  process.exitCode = 1;
});
