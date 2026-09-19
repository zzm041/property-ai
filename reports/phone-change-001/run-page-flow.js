"use strict";

const fs = require("fs");
const http = require("http");
const path = require("path");
const automator = require(process.env.MP_AUTOMATOR_MODULE);

const OUT = __dirname;
const API_PREFIX = "/api/v1";
const WS = process.env.MP_AUTOMATOR_WS || "ws://127.0.0.1:9435";
const evidence = {
  task_id: "PHONE-CHANGE-001",
  environment: {
    api: "http://127.0.0.1:8083/api/v1",
    database: "127.0.0.1:3307/property_ai_auth001_test",
    migration: "20260919_0009",
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
    .replace(/Bearer\s+\S+/gi, "Bearer [REDACTED]");
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
    const headers = { "X-Request-Id": "phone-change-001-page-flow" };
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

async function tapKind(page, kind) {
  for (const node of await page.$$("button")) {
    if ((await node.attribute("data-kind")) === kind) {
      await node.tap();
      return;
    }
  }
  throw new Error(`Phone code button not found: ${kind}`);
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

async function requestChangeCode(page, kind, phone) {
  await tapKind(page, kind);
  const key = kind === "old" ? "oldChallengePrompt" : "newChallengePrompt";
  const placeholder = kind === "old" ? "旧号挑战答案" : "新号挑战答案";
  const challenge = await waitData(
    page,
    (data) => Boolean(data[key]) && !data.loading,
    `${kind} phone visual challenge`
  );
  await fill(page, placeholder, challengeAnswer(challenge[key]));
  await tapKind(page, kind);
  await waitData(
    page,
    (data) =>
      !data.loading && String(data.phoneNotice || "").includes("手机号验证码已发送"),
    `${kind} phone SMS send`
  );
  return inbox(phone, "change_phone");
}

(async () => {
  save();
  const suffix = String((Date.now() + Math.floor(Math.random() * 10000)) % 100000000).padStart(8, "0");
  const oldPhone = `139${suffix}`;
  const newPhone = `188${suffix}`;
  const miniProgram = await automator.connect({ wsEndpoint: WS });
  activeMiniProgram = miniProgram;

  await resetToLogin(miniProgram);
  let page = await smsLogin(miniProgram, oldPhone);
  const oldTokens = await miniProgram.evaluate(() => ({
    access: wx.getStorageSync("access_token") || "",
    refresh: wx.getStorageSync("refresh_token") || "",
  }));
  const initialMe = await requestJson("GET", "/me", undefined, oldTokens.access);
  if (initialMe.status !== 200 || !initialMe.data || !initialMe.data.user_id) {
    throw new Error("Initial SMS login did not establish a user identity");
  }
  const userId = initialMe.data.user_id;

  await resetToLogin(miniProgram);
  page = await smsLogin(miniProgram, oldPhone);
  const changeTokens = await miniProgram.evaluate(() => ({
    access: wx.getStorageSync("access_token") || "",
    refresh: wx.getStorageSync("refresh_token") || "",
  }));
  const changeSessionMe = await requestJson(
    "GET",
    "/me",
    undefined,
    changeTokens.access
  );
  if (
    changeSessionMe.status !== 200 ||
    !changeSessionMe.data ||
    changeSessionMe.data.user_id !== userId
  ) {
    throw new Error("Second pre-change session did not use the same identity");
  }

  await tap(page, "账号与安全");
  page = await waitPath(miniProgram, "pages/profile/profile");
  await fill(page, "当前手机号", oldPhone);
  const oldCode = await requestChangeCode(page, "old", oldPhone);
  await fill(page, "旧号验证码", oldCode);
  await fill(page, "新手机号", newPhone);
  const newCode = await requestChangeCode(page, "new", newPhone);
  await fill(page, "新号验证码", newCode);
  const wrongOldCode = oldCode === "000000" ? "999999" : "000000";
  await fill(page, "旧号验证码", wrongOldCode);
  await tap(page, "确认更换手机号");
  const invalidAttempt = await waitData(
    page,
    (data) => Boolean(data.error) && !data.loading,
    "invalid old phone OTP"
  );
  const afterInvalidMe = await requestJson(
    "GET",
    "/me",
    undefined,
    changeTokens.access
  );
  evidence.checks.invalid_old_otp = {
    status: invalidAttempt.error && afterInvalidMe.status === 200 ? "PASS" : "FAIL",
    page_error_present: Boolean(invalidAttempt.error),
    current_session_status: afterInvalidMe.status,
    identity_unchanged: Boolean(
      afterInvalidMe.data && afterInvalidMe.data.user_id === userId
    ),
  };
  if (evidence.checks.invalid_old_otp.status !== "PASS") {
    throw new Error("Invalid old OTP did not fail safely");
  }

  await fill(page, "旧号验证码", oldCode);
  const concurrentRefreshPromise = requestJson("POST", "/auth/token/refresh", {
    refresh_token: oldTokens.refresh,
  });
  await tap(page, "确认更换手机号");
  const concurrentRefreshResult = await concurrentRefreshPromise;
  const changed = await waitData(
    page,
    (data) => data.phoneNotice === "手机号已变更，其他旧会话已失效",
    "phone change"
  );
  if (changed.oldPhone || changed.newPhone || changed.oldCode || changed.newCode) {
    throw new Error("Phone fields were not cleared after successful change");
  }
  await miniProgram.screenshot({ path: path.join(OUT, "01-phone-change-success.png") });
  evidence.screenshots.push("01-phone-change-success.png");

  const newTokens = await miniProgram.evaluate(() => ({
    access: wx.getStorageSync("access_token") || "",
    refresh: wx.getStorageSync("refresh_token") || "",
  }));
  const originalAccessResult = await requestJson(
    "GET",
    "/me",
    undefined,
    oldTokens.access
  );
  const originalRefreshResult = await requestJson("POST", "/auth/token/refresh", {
    refresh_token: oldTokens.refresh,
  });
  const changeAccessResult = await requestJson(
    "GET",
    "/me",
    undefined,
    changeTokens.access
  );
  const changeRefreshResult = await requestJson("POST", "/auth/token/refresh", {
    refresh_token: changeTokens.refresh,
  });
  const replacementMe = await requestJson("GET", "/me", undefined, newTokens.access);
  let refreshSuccessorAccessStatus = null;
  let refreshSuccessorRefreshStatus = null;
  if (
    concurrentRefreshResult.status === 200 &&
    concurrentRefreshResult.data &&
    concurrentRefreshResult.data.access_token &&
    concurrentRefreshResult.data.refresh_token
  ) {
    refreshSuccessorAccessStatus = (
      await requestJson(
        "GET",
        "/me",
        undefined,
        concurrentRefreshResult.data.access_token
      )
    ).status;
    refreshSuccessorRefreshStatus = (
      await requestJson("POST", "/auth/token/refresh", {
        refresh_token: concurrentRefreshResult.data.refresh_token,
      })
    ).status;
  }
  evidence.checks.phone_change = {
    status: "PASS",
    user_id: userId,
    old_phone_masked: maskPhone(oldPhone),
    new_phone_masked: maskPhone(newPhone),
    page_notice: changed.phoneNotice,
    sensitive_fields_cleared_before_screenshot: true,
    replacement_access_changed: oldTokens.access !== newTokens.access,
    replacement_refresh_changed: oldTokens.refresh !== newTokens.refresh,
  };
  evidence.checks.session_rotation = {
    status:
      originalAccessResult.status === 401 &&
      originalRefreshResult.status === 401 &&
      changeAccessResult.status === 401 &&
      changeRefreshResult.status === 401 &&
      (concurrentRefreshResult.status === 401 ||
        (refreshSuccessorAccessStatus === 401 &&
          refreshSuccessorRefreshStatus === 401)) &&
      replacementMe.status === 200 &&
      replacementMe.data.user_id === userId
        ? "PASS"
        : "FAIL",
    original_access_status: originalAccessResult.status,
    original_refresh_status: originalRefreshResult.status,
    change_access_status: changeAccessResult.status,
    change_refresh_status: changeRefreshResult.status,
    concurrent_refresh_status: concurrentRefreshResult.status,
    concurrent_refresh_successor_access_status: refreshSuccessorAccessStatus,
    concurrent_refresh_successor_refresh_status: refreshSuccessorRefreshStatus,
    replacement_access_status: replacementMe.status,
    replacement_user_id: replacementMe.data && replacementMe.data.user_id,
  };
  if (evidence.checks.session_rotation.status !== "PASS") {
    throw new Error("Old sessions were not revoked or replacement session was invalid");
  }

  await resetToLogin(miniProgram);
  page = await smsLogin(miniProgram, newPhone);
  const reloginTokens = await miniProgram.evaluate(() => ({
    access: wx.getStorageSync("access_token") || "",
  }));
  const reloginMe = await requestJson("GET", "/me", undefined, reloginTokens.access);
  await miniProgram.screenshot({ path: path.join(OUT, "02-new-phone-relogin-home.png") });
  evidence.screenshots.push("02-new-phone-relogin-home.png");
  evidence.checks.new_phone_relogin = {
    status:
      page.path.includes("pages/home/home") &&
      reloginMe.status === 200 &&
      reloginMe.data.user_id === userId
        ? "PASS"
        : "FAIL",
    page: page.path,
    http_status: reloginMe.status,
    user_id: reloginMe.data && reloginMe.data.user_id,
    same_identity: Boolean(reloginMe.data && reloginMe.data.user_id === userId),
  };
  if (evidence.checks.new_phone_relogin.status !== "PASS") {
    throw new Error("New phone did not log in to the same identity");
  }

  evidence.status = "PASS";
  save();
  console.log(`PHONE_CHANGE_PAGE_FLOW=PASS user_id=${userId}`);
  await miniProgram.disconnect();
  activeMiniProgram = null;
})().catch(async (error) => {
  evidence.status = "FAIL";
  evidence.failure = redact(error && (error.stack || error.message || error));
  save();
  fs.writeFileSync(path.join(OUT, "run-error.txt"), `${evidence.failure}\n`, "utf8");
  console.error(evidence.failure);
  if (activeMiniProgram) {
    try {
      await activeMiniProgram.disconnect();
    } catch (disconnectError) {
      console.error("Automator disconnect failed");
    }
  }
  process.exitCode = 1;
});
