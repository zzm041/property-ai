"use strict";

const crypto = require("crypto");
const fs = require("fs");
const http = require("http");
const path = require("path");
const automator = require(process.env.MP_AUTOMATOR_MODULE);

const REPO = path.resolve(__dirname, "..", "..");
const OUT = __dirname;
const PRIVATE_CONTEXT = path.join(
  REPO,
  "tmp",
  "notification-events-inbox-013",
  "context.json"
);
const RUNTIME_EVIDENCE = path.join(OUT, "runtime-evidence.json");
const PROJECT_CONFIG = path.join(REPO, "miniprogram", "project.config.json");
const WS = process.env.MP_AUTOMATOR_WS;
const CODE_SHA = process.env.MP_CODE_SHA;
const API_PREFIX = "/api/v1";
let privateRun = null;
let activeMiniProgram = null;
let requestMocked = false;
const requests = [];

function wait(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

function hash(value) {
  return crypto.createHash("sha256").update(String(value)).digest("hex").slice(0, 16);
}

function fileSha256(filename) {
  return crypto.createHash("sha256").update(fs.readFileSync(filename)).digest("hex").toUpperCase();
}

function redactError(error) {
  let text = error && error.stack ? error.stack : String(error || "Unknown error");
  if (privateRun && privateRun.phones) {
    for (const phone of Object.values(privateRun.phones)) {
      text = text.split(String(phone)).join("[REDACTED_PHONE]");
    }
  }
  return text
    .replace(/\b1[3-9]\d{9}\b/g, "[REDACTED_PHONE]")
    .replace(/Bearer\s+\S+/gi, "Bearer [REDACTED]")
    .replace(/eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+/g, "[REDACTED_TOKEN]");
}

async function tolerateTimeout(operation) {
  try {
    return await operation();
  } catch (error) {
    if (!String(error && error.message).includes("timeout waiting for automator response")) {
      throw error;
    }
    await wait(600);
    return null;
  }
}

async function current(miniProgram) {
  return miniProgram.currentPage();
}

async function waitPath(miniProgram, fragment, attempts = 120) {
  const expected = String(fragment).replace(/^\//, "");
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    const page = await current(miniProgram);
    if (page && page.path && page.path.includes(expected)) return page;
    await wait(250);
  }
  const page = await current(miniProgram);
  throw new Error(`Expected page ${expected}; current=${page && page.path}`);
}

async function waitData(page, predicate, label, attempts = 120, ignoreError = false) {
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    const data = await page.data();
    if (predicate(data)) return data;
    if (!ignoreError && data.error && !data.loading) throw new Error(`${label}: ${data.error}`);
    await wait(250);
  }
  throw new Error(`${label} timed out`);
}

async function byAttribute(page, selector, attribute, expected) {
  for (const node of await page.$$(selector)) {
    if (String(await node.attribute(attribute)) === String(expected)) return node;
  }
  throw new Error(`${selector} ${attribute}=${expected} not found`);
}

async function byText(page, selector, expected, exact = false) {
  for (const node of await page.$$(selector)) {
    const value = ((await node.text()) || "").replace(/\s+/g, " ").trim();
    if ((exact && value === expected) || (!exact && value.includes(expected))) return node;
  }
  throw new Error(`${selector} text=${expected} not found`);
}

async function fillPlaceholder(page, placeholder, value) {
  const fields = [...(await page.$$("input")), ...(await page.$$("textarea"))];
  for (const field of fields) {
    if ((await field.attribute("placeholder")) === placeholder) {
      await field.input(String(value));
      return;
    }
  }
  throw new Error(`Input not found: ${placeholder}`);
}

async function tapForPath(miniProgram, element, fragment) {
  await tolerateTimeout(() => element.tap());
  return waitPath(miniProgram, fragment);
}

function answer(prompt) {
  const match = String(prompt || "").match(/^(\d+)\s*\+\s*(\d+)\s*=\s*\?$/);
  if (!match) throw new Error("Unsupported Development challenge prompt");
  return String(Number(match[1]) + Number(match[2]));
}

function requestJson(method, pathname, body) {
  return new Promise((resolve, reject) => {
    const encoded = JSON.stringify(body || {});
    const request = http.request(
      {
        host: "127.0.0.1",
        port: 8083,
        method,
        path: `${API_PREFIX}${pathname}`,
        headers: {
          "Content-Type": "application/json",
          "Content-Length": Buffer.byteLength(encoded),
          "X-Request-Id": `${privateRun.run_id}-page-inbox-${Date.now()}`,
        },
      },
      (response) => {
        let raw = "";
        response.on("data", (chunk) => {
          raw += chunk;
        });
        response.on("end", () => {
          try {
            resolve({ status: response.statusCode, payload: raw ? JSON.parse(raw) : {} });
          } catch (error) {
            reject(new Error(`Invalid JSON from ${pathname}`));
          }
        });
      }
    );
    request.on("error", reject);
    request.write(encoded);
    request.end();
  });
}

async function developmentCode(phone) {
  const response = await requestJson("POST", "/auth/sms/test-inbox", {
    phone,
    purpose: "login",
  });
  const code = response.payload && response.payload.data && response.payload.data.code;
  if (response.status !== 200 || !code) {
    throw new Error("Development SMS inbox did not return the current login code");
  }
  return String(code);
}

async function resetToLogin(miniProgram) {
  await miniProgram.callWxMethod("clearStorageSync");
  await miniProgram.callWxMethod("reLaunch", { url: "/pages/login/login" });
  return waitPath(miniProgram, "pages/login/login");
}

async function smsLogin(miniProgram, identity) {
  const phone = String(privateRun.phones[identity]);
  let page = await resetToLogin(miniProgram);
  const initial = await page.data();
  if (initial.mode !== "password") throw new Error("Login did not start in password mode");
  await (await byText(page, ".link-text", "验证码登录")).tap();
  await waitData(page, (state) => state.mode === "sms", "SMS login mode");
  await fillPlaceholder(page, "手机号", phone);
  await tolerateTimeout(() => byText(page, "button", "获取验证码", true).then((item) => item.tap()));
  let state = await waitData(
    page,
    (value) => Boolean(value.challengePrompt) && !value.loading,
    "Development challenge"
  );
  const challengeAnswer = answer(state.challengePrompt);
  await fillPlaceholder(page, "挑战答案", challengeAnswer);
  await waitData(
    page,
    (value) => value.challengeAnswer === challengeAnswer,
    "Development challenge input",
    40,
    true
  );
  await tolerateTimeout(() => byText(page, "button", "获取验证码", true).then((item) => item.tap()));
  await waitData(
    page,
    (value) => !value.challengePrompt && Number(value.cooldown) > 0 && !value.loading,
    "Development SMS delivery",
    120,
    true
  );
  await fillPlaceholder(page, "短信验证码", await developmentCode(phone));
  page = await tapForPath(miniProgram, await byText(page, "button", "登录", true), "pages/home/home");
  state = await waitData(
    page,
    (value) => Boolean(value.me && value.me.user_id) && !value.loading,
    `${identity} home identity`
  );
  if (Number(state.me.user_id) !== Number(privateRun.identities[identity])) {
    throw new Error(`Page login returned a different ${identity} identity`);
  }
  return page;
}

async function attachRequestLogger(miniProgram) {
  await miniProgram.exposeFunction("recordNtf013Request", (info) => {
    if (!info || !info.url) return;
    const relative = String(info.url).replace(/^https?:\/\/[^/]+/, "");
    if (!relative.startsWith(API_PREFIX)) return;
    const visible = relative.slice(API_PREFIX.length);
    if (
      !visible.startsWith("/notifications") &&
      !visible.startsWith("/work-orders") &&
      !visible.startsWith("/announcements") &&
      !visible.startsWith("/auth/sms/login") &&
      visible !== "/me"
    ) {
      return;
    }
    requests.push({
      method: info.method || "GET",
      path: visible,
      status: Number(info.status),
      received_at_local: new Date().toISOString(),
      source: "wx.request proxy forwarding the actual request",
    });
  });
  await miniProgram.mockWxMethod("request", function recordRequest(options) {
    const self = this;
    return new Promise((resolve, reject) => {
      self.origin({
        ...options,
        success(response) {
          if (typeof recordNtf013Request === "function") {
            recordNtf013Request({
              url: options.url,
              method: options.method,
              status: response.statusCode,
            });
          }
          if (options.success) options.success(response);
          resolve(response);
        },
        fail(error) {
          if (options.fail) options.fail(error);
          reject(error);
        },
      });
    });
  });
  requestMocked = true;
}

async function screenshot(miniProgram, name) {
  await wait(700);
  await miniProgram.screenshot({ path: path.join(OUT, name) });
}

async function openNotifications(miniProgram, home) {
  const entry = await byAttribute(home, "button", "data-url", "/pages/notifications/list");
  const page = await tapForPath(miniProgram, entry, "pages/notifications/list");
  await waitData(
    page,
    (state) => !state.loading && Array.isArray(state.items) && !state.error,
    "notification list"
  );
  return page;
}

function idsOf(state) {
  return (state.items || []).map((item) => Number(item.id));
}

function assertDescendingUnique(ids, label) {
  if (new Set(ids).size !== ids.length) throw new Error(`${label} contains duplicate IDs`);
  for (let index = 1; index < ids.length; index += 1) {
    if (ids[index] >= ids[index - 1]) throw new Error(`${label} is not strictly descending`);
  }
}

function matchingRequests(method, prefix) {
  return requests.filter((item) => item.method === method && item.path.startsWith(prefix));
}

async function clickNotification(miniProgram, page, notificationId, expectedPath) {
  const card = await page.$(`#notification-${notificationId}`);
  if (!card) throw new Error(`Notification ${notificationId} was not rendered`);
  await tolerateTimeout(() => card.tap());
  return waitPath(miniProgram, expectedPath);
}

(async () => {
  if (!WS || !CODE_SHA) throw new Error("Missing automator evidence environment");
  privateRun = JSON.parse(fs.readFileSync(PRIVATE_CONTEXT, "utf8"));
  const runtime = JSON.parse(fs.readFileSync(RUNTIME_EVIDENCE, "utf8"));
  if (runtime.run_id !== privateRun.run_id || runtime.code_sha !== CODE_SHA) {
    throw new Error("Runtime/page evidence version mismatch");
  }
  const workOrderId = Number(privateRun.objects.work_order_id);
  const announcementId = Number(privateRun.objects.announcement_id);
  const announcementNotificationId = Number(
    privateRun.objects.resident_announcement_notification_id
  );
  const workOrderNotificationId = Math.max(
    ...privateRun.objects.resident_work_order_notification_ids
      .map(Number)
      .filter((value) => value !== Number(privateRun.objects.resident_read_notification_id))
  );
  const configHashBefore = fileSha256(PROJECT_CONFIG);
  const screenshots = [];
  const evidence = {
    task_id: "NOTIFICATION-EVENTS-INBOX-013",
    captured_at: new Date().toISOString(),
    code_sha: CODE_SHA,
    run_id: privateRun.run_id,
    environment: {
      interaction: "WeChat DevTools miniprogram-automator through actual page controls",
      api: "http://127.0.0.1:8083/api/v1",
      database: "127.0.0.1:3307/property_ai_auth001_test",
      revision: "20260919_0009",
      rabbit_vhost: "property_ai_auth001",
      rabbit_queue: "property_ai.auth001",
      provider: "DEVELOPMENT_SMS_AND_FAKE_AI_AND_DEVELOPMENT_NOTIFICATION",
      automator_endpoint: WS,
    },
    objects: {
      work_order_id: workOrderId,
      work_order_notification_id: workOrderNotificationId,
      announcement_id: announcementId,
      announcement_notification_id: announcementNotificationId,
    },
    checks: {},
    requests,
    screenshots,
    boundaries: {
      normal_sms_page_login: true,
      development_sms_test_inbox_used: true,
      seed_or_dev_token_login: false,
      page_state_faked: false,
      wx_request_forwarded_to_real_api: true,
      direct_database_business_write: false,
      shared_vhost_touched: false,
      real_model_claimed: false,
      production_claimed: false,
    },
  };

  const miniProgram = await automator.connect({ wsEndpoint: WS });
  activeMiniProgram = miniProgram;
  await attachRequestLogger(miniProgram);

  let home = await smsLogin(miniProgram, "staff");
  let page = await openNotifications(miniProgram, home);
  let state = await waitData(
    page,
    (value) => !value.loading && value.items.length === 20 && value.hasMore && value.nextCursor,
    "staff first notification page"
  );
  const firstIds = idsOf(state);
  assertDescendingUnique(firstIds, "staff first page");
  const firstCursor = Number(state.nextCursor);
  if (firstCursor !== firstIds[firstIds.length - 1]) {
    throw new Error("First notification cursor does not match its final item");
  }
  await screenshot(miniProgram, "01-staff-notifications-first-page.png");
  screenshots.push("01-staff-notifications-first-page.png");

  const loadMore = await page.$("#notification-load-more");
  if (!loadMore) throw new Error("Notification load-more control is missing");
  await tolerateTimeout(() => loadMore.tap());
  state = await waitData(
    page,
    (value) => !value.loading && value.items.length > 20 && !value.hasMore,
    "staff final notification page"
  );
  const allIds = idsOf(state);
  const loadedAllState = state;
  assertDescendingUnique(allIds, "staff loaded notifications");
  if (!firstIds.every((id, index) => allIds[index] === id)) {
    throw new Error("Loading more replaced or reordered the first notification page");
  }
  if (!allIds.slice(firstIds.length).every((id) => id < firstCursor)) {
    throw new Error("Notification continuation did not stay below the cursor");
  }
  await tolerateTimeout(() =>
    miniProgram.callWxMethod("pageScrollTo", { scrollTop: 99999, duration: 0 })
  );
  await screenshot(miniProgram, "02-staff-notifications-loaded-all.png");
  screenshots.push("02-staff-notifications-loaded-all.png");

  home = await smsLogin(miniProgram, "primary");
  page = await openNotifications(miniProgram, home);
  state = await page.data();
  const workItem = state.items.find((item) => Number(item.id) === workOrderNotificationId);
  const announcementItem = state.items.find(
    (item) => Number(item.id) === announcementNotificationId
  );
  if (!workItem || workItem.read_at || !announcementItem || announcementItem.read_at) {
    throw new Error("Fresh resident notification targets were not both unread and visible");
  }
  await screenshot(miniProgram, "03-resident-notifications-unread.png");
  screenshots.push("03-resident-notifications-unread.png");

  const workReadOffset = requests.length;
  let detail = await clickNotification(
    miniProgram,
    page,
    workOrderNotificationId,
    "pages/work-orders/detail"
  );
  const workDetailState = await waitData(
    detail,
    (value) =>
      !value.loading &&
      value.workOrder &&
      Number(value.workOrder.id) === workOrderId &&
      value.workOrder.status === "CLOSED" &&
      !value.error,
    "resident notification work-order detail"
  );
  if (
    matchingRequests("POST", `/notifications/${workOrderNotificationId}/read`).length !== 1 ||
    !requests.slice(workReadOffset).some(
      (item) => item.method === "GET" && item.path === `/work-orders/${workOrderId}`
    )
  ) {
    throw new Error("Work-order notification did not mark read and fetch the same resource");
  }
  await screenshot(miniProgram, "04-resident-work-order-from-notification.png");
  screenshots.push("04-resident-work-order-from-notification.png");

  await miniProgram.navigateBack();
  page = await waitPath(miniProgram, "pages/notifications/list");
  state = await waitData(
    page,
    (value) =>
      !value.loading &&
      value.items.some(
        (item) => Number(item.id) === workOrderNotificationId && Boolean(item.read_at)
      ),
    "work-order notification read state after navigation refresh"
  );

  const announcementReadOffset = requests.length;
  detail = await clickNotification(
    miniProgram,
    page,
    announcementNotificationId,
    "pages/announcements/detail"
  );
  const announcementDetailState = await waitData(
    detail,
    (value) =>
      !value.loading &&
      value.status === "PUBLISHED" &&
      value.title === runtime.database.announcement.title &&
      !value.error,
    "resident notification announcement detail"
  );
  if (
    matchingRequests("POST", `/notifications/${announcementNotificationId}/read`).length !== 1 ||
    !requests.slice(announcementReadOffset).some(
      (item) =>
        item.method === "GET" &&
        item.path.includes(`/announcements?property_id=${privateRun.org.property_id}`) &&
        item.path.includes(`after_id=${announcementId - 1}`) &&
        item.path.includes("limit=1")
    )
  ) {
    throw new Error("Announcement notification did not mark read and fetch the exact resource");
  }
  await screenshot(miniProgram, "05-resident-announcement-from-notification.png");
  screenshots.push("05-resident-announcement-from-notification.png");

  home = await smsLogin(miniProgram, "primary");
  page = await openNotifications(miniProgram, home);
  state = await waitData(
    page,
    (value) => {
      const work = value.items.find((item) => Number(item.id) === workOrderNotificationId);
      const announcement = value.items.find(
        (item) => Number(item.id) === announcementNotificationId
      );
      return !value.loading && work && work.read_at && announcement && announcement.read_at;
    },
    "read state after resident relogin"
  );
  await screenshot(miniProgram, "06-resident-read-state-after-relogin.png");
  screenshots.push("06-resident-read-state-after-relogin.png");

  const configHashAfter = fileSha256(PROJECT_CONFIG);
  if (configHashAfter !== configHashBefore) {
    throw new Error("project.config.json changed during page verification");
  }
  evidence.checks = {
    staff_identity: Number(privateRun.identities.staff),
    resident_identity: Number(privateRun.identities.primary),
    first_page_count: firstIds.length,
    first_page_cursor: firstCursor,
    loaded_total_count: allIds.length,
    second_page_count: allIds.length - firstIds.length,
    descending_unique_without_overlap: true,
    final_page_has_no_cursor: !loadedAllState.hasMore && !loadedAllState.nextCursor,
    work_order_mark_read_request_count: matchingRequests(
      "POST",
      `/notifications/${workOrderNotificationId}/read`
    ).length,
    work_order_resource_id_matches: Number(workDetailState.workOrder.id) === workOrderId,
    work_order_history_status: workDetailState.workOrder.status,
    announcement_mark_read_request_count: matchingRequests(
      "POST",
      `/notifications/${announcementNotificationId}/read`
    ).length,
    announcement_resource_id_matches:
      announcementDetailState.title === runtime.database.announcement.title,
    announcement_status: announcementDetailState.status,
    read_state_refresh_persisted: true,
    read_state_relogin_persisted: true,
    project_config_sha256_before: configHashBefore,
    project_config_sha256_after: configHashAfter,
  };
  fs.writeFileSync(
    path.join(OUT, "page-evidence.json"),
    `${JSON.stringify(evidence, null, 2)}\n`,
    "utf8"
  );
  console.log(
    JSON.stringify({
      status: "PASS",
      work_order_id: workOrderId,
      announcement_id: announcementId,
      screenshots,
      request_count: requests.length,
    })
  );

  await miniProgram.restoreWxMethod("request");
  requestMocked = false;
  await miniProgram.disconnect();
  activeMiniProgram = null;
})().catch(async (error) => {
  const safe = redactError(error);
  fs.writeFileSync(path.join(OUT, "page-run-error.txt"), `${safe}\n`, "utf8");
  if (activeMiniProgram) {
    try {
      if (requestMocked) await activeMiniProgram.restoreWxMethod("request");
    } catch (restoreError) {
      fs.appendFileSync(
        path.join(OUT, "page-run-error.txt"),
        `${redactError(restoreError)}\n`,
        "utf8"
      );
    }
    try {
      await activeMiniProgram.disconnect();
    } catch (disconnectError) {
      fs.appendFileSync(
        path.join(OUT, "page-run-error.txt"),
        `${redactError(disconnectError)}\n`,
        "utf8"
      );
    }
  }
  console.error(safe);
  process.exitCode = 1;
});
