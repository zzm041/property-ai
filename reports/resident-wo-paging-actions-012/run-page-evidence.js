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
  "resident-wo-paging-actions-012",
  "context.json"
);
const WS = process.env.MP_AUTOMATOR_WS;
const CODE_SHA = process.env.MP_CODE_SHA;
const API_LOG = process.env.MP_API_LOG;
const API_PREFIX = "/api/v1";
let privateRun = null;
let activeMiniProgram = null;
let requestMocked = false;
let modalMocked = false;
const requests = [];

function wait(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

function hash(value) {
  return crypto.createHash("sha256").update(String(value)).digest("hex").slice(0, 16);
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
    await wait(500);
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

async function smsLogin(miniProgram) {
  const phone = String(privateRun.phones.primary);
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
    "resident home identity"
  );
  if (Number(state.me.user_id) !== Number(privateRun.identities.primary)) {
    throw new Error("Page login returned a different synthetic resident identity");
  }
  return page;
}

async function attachRequestLogger(miniProgram) {
  await miniProgram.exposeFunction("recordWo012Request", (info) => {
    if (!info || !info.url) return;
    const relative = String(info.url).replace(/^https?:\/\/[^/]+/, "");
    if (!relative.startsWith(API_PREFIX)) return;
    const visible = relative.slice(API_PREFIX.length);
    if (!visible.startsWith("/work-orders") && !visible.startsWith("/auth/sms/login")) return;
    requests.push({
      method: info.method || "GET",
      path: visible,
      status: Number(info.status),
      received_at_local: new Date().toISOString(),
    });
  });
  await miniProgram.mockWxMethod("request", function recordRequest(options) {
    const self = this;
    return new Promise((resolve, reject) => {
      self.origin({
        ...options,
        success(response) {
          if (typeof recordWo012Request === "function") {
            recordWo012Request({
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

async function attachModalSequence(miniProgram) {
  await miniProgram.evaluate(() => {
    delete globalThis.__wo012ModalCount;
  });
  await miniProgram.mockWxMethod("showModal", function modalChoice(options) {
    globalThis.__wo012ModalCount = (globalThis.__wo012ModalCount || 0) + 1;
    const confirm = globalThis.__wo012ModalCount > 1;
    const result = {
      confirm,
      cancel: !confirm,
      errMsg: "showModal:ok",
    };
    if (options.success) options.success(result);
    if (options.complete) options.complete(result);
    return Promise.resolve(result);
  });
  modalMocked = true;
}

function completedApiLogRequests(method, pathname, status) {
  return fs
    .readFileSync(API_LOG, "utf8")
    .split(/\r?\n/)
    .filter(Boolean)
    .map((line) => {
      try {
        return JSON.parse(line);
      } catch (error) {
        return null;
      }
    })
    .filter(
      (event) =>
        event &&
        event.event === "request_completed" &&
        event.method === method &&
        event.path === `${API_PREFIX}${pathname}` &&
        Number(event.status_code) === status
    )
    .map((event) => ({
      method,
      path: pathname,
      status,
      request_id_hash: hash(event.request_id),
      source: "redacted API log from interrupted page run",
    }));
}

async function screenshot(miniProgram, name) {
  await wait(800);
  await miniProgram.screenshot({ path: path.join(OUT, name) });
}

async function openListFromHome(miniProgram, home) {
  const entry = await byAttribute(home, "button", "data-url", "/pages/work-orders/list");
  const page = await tapForPath(miniProgram, entry, "pages/work-orders/list");
  return waitData(
    page,
    (state) => !state.loading && Array.isArray(state.items) && !state.error,
    "resident work-order list"
  ).then(() => page);
}

async function waitFirstPage(page) {
  return waitData(
    page,
    (state) => !state.loading && state.items && state.items.length === 20 && state.hasMore,
    "first work-order page"
  );
}

async function openCard(miniProgram, page, workOrderId) {
  const card = await byAttribute(page, ".card", "data-id", workOrderId);
  const detail = await tapForPath(miniProgram, card, "pages/work-orders/detail");
  await waitData(
    detail,
    (state) =>
      !state.loading &&
      state.workOrder &&
      Number(state.workOrder.id) === Number(workOrderId) &&
      !state.error,
    `work-order ${workOrderId} detail`
  );
  return detail;
}

async function backToFirstPage(miniProgram) {
  await miniProgram.navigateBack();
  const page = await waitPath(miniProgram, "pages/work-orders/list");
  await waitFirstPage(page);
  return page;
}

function idsOf(state) {
  return (state.items || []).map((item) => Number(item.id));
}

function assertAscendingUnique(ids, label) {
  if (new Set(ids).size !== ids.length) throw new Error(`${label} contains duplicate IDs`);
  for (let index = 1; index < ids.length; index += 1) {
    if (ids[index] <= ids[index - 1]) throw new Error(`${label} is not strictly ascending`);
  }
}

function matchingRequests(method, pathValue) {
  return requests.filter((item) => item.method === method && item.path === pathValue);
}

(async () => {
  if (!WS || !CODE_SHA || !API_LOG) throw new Error("Missing automator evidence environment");
  privateRun = JSON.parse(fs.readFileSync(PRIVATE_CONTEXT, "utf8"));
  const objects = privateRun.objects;
  const appendId = Number(objects.append_page_id);
  const cancelId = Number(objects.cancel_page_id);
  const ratingId = Number(objects.rating_page_id);
  const appendMarker = `${privateRun.run_id}:page-append`;
  const cancelReason = `${privateRun.run_id}:page-cancel`;
  const ratingComment = `${privateRun.run_id}:page-rating`;
  const screenshots = [];

  const evidence = {
    task_id: "RESIDENT-WO-PAGING-ACTIONS-012",
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
      worker_during_actions: "intentionally paused after verified zero ready/unacked",
      provider: "DEVELOPMENT_SMS_AND_FAKE_AI",
      automator_endpoint: WS,
      api_log: path.relative(REPO, API_LOG).replace(/\\/g, "/"),
    },
    objects: {
      append_id: appendId,
      cancel_id: cancelId,
      rating_id: ratingId,
      append_marker_hash: hash(appendMarker),
      cancel_reason_hash: hash(cancelReason),
      rating_comment_hash: hash(ratingComment),
    },
    checks: {},
    requests,
    screenshots,
    boundaries: {
      normal_sms_page_login: true,
      development_sms_test_inbox_used: true,
      seed_or_dev_token_login: false,
      page_state_faked: false,
      direct_database_business_write: false,
      modal_driver:
        "automator showModal method mock returned one cancel then one confirm; native modal visual not claimed",
      rating_picker_driver: "supported automator change event; native picker overlay not claimed",
      real_model_claimed: false,
      production_claimed: false,
    },
  };

  const miniProgram = await automator.connect({ wsEndpoint: WS });
  activeMiniProgram = miniProgram;
  await attachRequestLogger(miniProgram);

  const home = await smsLogin(miniProgram);
  let page = await openListFromHome(miniProgram, home);
  let state = await waitFirstPage(page);
  const firstIds = idsOf(state);
  assertAscendingUnique(firstIds, "first page");
  if (!state.nextCursor || Number(state.nextCursor) !== firstIds[firstIds.length - 1]) {
    throw new Error("First-page cursor does not match its final item");
  }
  const firstCursor = Number(state.nextCursor);
  await screenshot(miniProgram, "01-list-first-page.png");
  screenshots.push("01-list-first-page.png");

  await tolerateTimeout(() =>
    byAttribute(page, "button", "data-action", "load-more").then((button) => button.tap())
  );
  state = await waitData(
    page,
    (value) => !value.loading && value.items && value.items.length === 22 && !value.hasMore,
    "loaded final work-order page"
  );
  const allIds = idsOf(state);
  assertAscendingUnique(allIds, "loaded list");
  if (!firstIds.every((id, index) => allIds[index] === id)) {
    throw new Error("Loading more replaced or reordered the first page");
  }
  if (!allIds.slice(firstIds.length).every((id) => id > firstCursor)) {
    throw new Error("Loaded IDs did not advance past the cursor");
  }
  if (!matchingRequests("GET", `/work-orders?limit=20&after_id=${firstCursor}`).length) {
    throw new Error("Load-more did not issue the expected cursor request");
  }
  await tolerateTimeout(() =>
    miniProgram.callWxMethod("pageScrollTo", { scrollTop: 99999, duration: 0 })
  );
  await screenshot(miniProgram, "02-list-loaded-all.png");
  screenshots.push("02-list-loaded-all.png");

  page = await openCard(miniProgram, page, appendId);
  state = await page.data();
  if (!state.canAppend || state.workOrder.status !== "PENDING_ANALYSIS") {
    throw new Error("Append target is not in the expected pending page state");
  }
  const appendWasRecovered = String((state.item && state.item.extra_notes) || "").includes(
    appendMarker
  );
  if (appendWasRecovered) {
    const priorAppend = completedApiLogRequests(
      "POST",
      `/work-orders/${appendId}/append`,
      200
    );
    if (priorAppend.length !== 1) {
      throw new Error("Interrupted page run did not leave exactly one successful append request");
    }
    requests.push(...priorAppend);
    if (!fs.existsSync(path.join(OUT, "03-append-form.png"))) {
      throw new Error("Interrupted append form screenshot is missing");
    }
    screenshots.push("03-append-form.png");
  } else {
    await (await byAttribute(page, "textarea", "data-field", "appendText")).input(appendMarker);
    await screenshot(miniProgram, "03-append-form.png");
    screenshots.push("03-append-form.png");
    const appendBefore = matchingRequests("POST", `/work-orders/${appendId}/append`).length;
    await tolerateTimeout(() =>
      byAttribute(page, "button", "data-action", "append").then((button) => button.tap())
    );
    state = await waitData(
      page,
      (value) =>
        !value.loading &&
        value.item &&
        String(value.item.extra_notes || "").includes(appendMarker) &&
        value.actionNotice === "补充内容已保存",
      "page append persistence"
    );
    if (matchingRequests("POST", `/work-orders/${appendId}/append`).length !== appendBefore + 1) {
      throw new Error("Append page action did not issue exactly one request");
    }
  }
  await screenshot(miniProgram, "04-append-saved.png");
  screenshots.push("04-append-saved.png");
  await tolerateTimeout(() =>
    byAttribute(page, "button", "data-action", "refresh").then((button) => button.tap())
  );
  await waitData(
    page,
    (value) => !value.loading && String((value.item && value.item.extra_notes) || "").includes(appendMarker),
    "append after refresh"
  );

  page = await backToFirstPage(miniProgram);
  page = await openCard(miniProgram, page, cancelId);
  state = await page.data();
  if (!state.canCancel || state.workOrder.status !== "PENDING_ANALYSIS") {
    throw new Error("Cancel target is not in the expected pending page state");
  }
  await (await byAttribute(page, "textarea", "data-field", "cancelReason")).input(cancelReason);
  await screenshot(miniProgram, "05-cancel-form.png");
  screenshots.push("05-cancel-form.png");
  await attachModalSequence(miniProgram);
  const cancelBefore = matchingRequests("POST", `/work-orders/${cancelId}/cancel`).length;
  await tolerateTimeout(() =>
    byAttribute(page, "button", "data-action", "cancel").then((button) => button.tap())
  );
  await wait(500);
  state = await page.data();
  if (state.workOrder.status !== "PENDING_ANALYSIS") {
    throw new Error("Cancelling the confirmation modal changed the work-order status");
  }
  if (matchingRequests("POST", `/work-orders/${cancelId}/cancel`).length !== cancelBefore) {
    throw new Error("Cancelling the confirmation modal sent a cancel request");
  }
  await tolerateTimeout(() =>
    byAttribute(page, "button", "data-action", "cancel").then((button) => button.tap())
  );
  state = await waitData(
    page,
    (value) =>
      !value.loading &&
      value.workOrder &&
      value.workOrder.status === "CANCELLED" &&
      (value.logs || []).some(
        (item) => item.event_code === "CANCEL" && item.reason === cancelReason
      ),
    "page cancel persistence"
  );
  if (matchingRequests("POST", `/work-orders/${cancelId}/cancel`).length !== cancelBefore + 1) {
    throw new Error("Confirmed cancel did not issue exactly one request");
  }
  await miniProgram.restoreWxMethod("showModal");
  modalMocked = false;
  await screenshot(miniProgram, "06-cancelled-history.png");
  screenshots.push("06-cancelled-history.png");

  page = await backToFirstPage(miniProgram);
  page = await openCard(miniProgram, page, ratingId);
  state = await page.data();
  if (!state.canRate || state.workOrder.status !== "CLOSED" || state.latestRating) {
    throw new Error("Rating target is not an unrated CLOSED order");
  }
  await (await byAttribute(page, "picker", "data-field", "rating")).trigger("change", {
    value: 2,
  });
  await (await byAttribute(page, "textarea", "data-field", "ratingComment")).input(
    ratingComment
  );
  await screenshot(miniProgram, "07-rating-form.png");
  screenshots.push("07-rating-form.png");
  const ratingBefore = matchingRequests("POST", `/work-orders/${ratingId}/rate`).length;
  await tolerateTimeout(() =>
    byAttribute(page, "button", "data-action", "rate").then((button) => button.tap())
  );
  state = await waitData(
    page,
    (value) =>
      !value.loading &&
      value.latestRating &&
      Number(value.latestRating.rating) === 3 &&
      value.latestRating.comment === ratingComment &&
      !value.canRate,
    "page rating persistence"
  );
  if (matchingRequests("POST", `/work-orders/${ratingId}/rate`).length !== ratingBefore + 1) {
    throw new Error("Rating page action did not issue exactly one request");
  }
  await screenshot(miniProgram, "08-rating-saved.png");
  screenshots.push("08-rating-saved.png");
  await tolerateTimeout(() =>
    byAttribute(page, "button", "data-action", "refresh").then((button) => button.tap())
  );
  await waitData(
    page,
    (value) => !value.loading && value.latestRating && Number(value.latestRating.rating) === 3,
    "rating after refresh"
  );

  const reloginHome = await smsLogin(miniProgram);
  page = await openListFromHome(miniProgram, reloginHome);
  state = await waitFirstPage(page);
  if (idsOf(state).length !== 20) throw new Error("Relogin first page size changed");

  page = await openCard(miniProgram, page, appendId);
  state = await page.data();
  if (!String(state.item.extra_notes || "").includes(appendMarker)) {
    throw new Error("Append marker did not survive relogin");
  }
  await screenshot(miniProgram, "09-relogin-append.png");
  screenshots.push("09-relogin-append.png");

  page = await backToFirstPage(miniProgram);
  page = await openCard(miniProgram, page, cancelId);
  state = await page.data();
  if (
    state.workOrder.status !== "CANCELLED" ||
    !(state.logs || []).some((item) => item.event_code === "CANCEL" && item.reason === cancelReason)
  ) {
    throw new Error("Cancelled state/history did not survive relogin");
  }
  await screenshot(miniProgram, "10-relogin-cancelled.png");
  screenshots.push("10-relogin-cancelled.png");

  page = await backToFirstPage(miniProgram);
  page = await openCard(miniProgram, page, ratingId);
  state = await page.data();
  if (
    !state.latestRating ||
    Number(state.latestRating.rating) !== 3 ||
    state.latestRating.comment !== ratingComment ||
    state.canRate
  ) {
    throw new Error("Rating did not survive relogin or duplicate action remained available");
  }
  await screenshot(miniProgram, "11-relogin-rating.png");
  screenshots.push("11-relogin-rating.png");

  page = await backToFirstPage(miniProgram);
  const reloginCursor = Number((await page.data()).nextCursor);
  await tolerateTimeout(() =>
    byAttribute(page, "button", "data-action", "load-more").then((button) => button.tap())
  );
  state = await waitData(
    page,
    (value) => !value.loading && value.items && value.items.length === 22 && !value.hasMore,
    "relogin load-more persistence"
  );
  assertAscendingUnique(idsOf(state), "relogin loaded list");
  await tolerateTimeout(() =>
    miniProgram.callWxMethod("pageScrollTo", { scrollTop: 99999, duration: 0 })
  );
  await screenshot(miniProgram, "12-relogin-pagination.png");
  screenshots.push("12-relogin-pagination.png");

  evidence.checks = {
    resident_identity: Number(privateRun.identities.primary),
    first_page_count: firstIds.length,
    first_page_cursor: firstCursor,
    loaded_total_count: allIds.length,
    second_page_count: allIds.length - firstIds.length,
    cursor_request_observed: true,
    append_page_single_request: true,
    append_recovered_from_interrupted_page_run: appendWasRecovered,
    append_refresh_persisted: true,
    cancel_modal_cancel_sent_no_request: true,
    cancel_modal_confirm_single_request: true,
    cancel_reason_rendered_in_history: true,
    rating_selected: 3,
    rating_page_single_request: true,
    rating_refresh_persisted: true,
    relogin_append_persisted: true,
    relogin_cancel_and_history_persisted: true,
    relogin_rating_persisted_and_action_hidden: true,
    relogin_cursor: reloginCursor,
    relogin_final_count: state.items.length,
  };
  fs.writeFileSync(
    path.join(OUT, "page-evidence.json"),
    `${JSON.stringify(evidence, null, 2)}\n`,
    "utf8"
  );
  console.log(JSON.stringify(evidence, null, 2));

  await miniProgram.restoreWxMethod("request");
  requestMocked = false;
  await miniProgram.disconnect();
  activeMiniProgram = null;
})().catch(async (error) => {
  const safe = redactError(error);
  fs.writeFileSync(path.join(OUT, "page-run-error.txt"), `${safe}\n`, "utf8");
  if (activeMiniProgram) {
    try {
      if (modalMocked) await activeMiniProgram.restoreWxMethod("showModal");
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
