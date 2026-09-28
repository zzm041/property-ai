"use strict";

const crypto = require("crypto");
const fs = require("fs");
const http = require("http");
const path = require("path");
const automator = require(
  "C:/Users/lmj/AppData/Local/Temp/mp-automator-npm/node_modules/miniprogram-automator"
);

const WS = process.env.MP_AUTOMATOR_WS || "ws://127.0.0.1:9440";
const PHONE = process.env.DELIVERY_RAG_PHONE;
const PASSWORD = process.env.DELIVERY_RAG_PASSWORD;
const RUN_ID = process.env.DELIVERY_RAG_RUN_ID;
const MODE = process.env.DELIVERY_RAG_MODE || "full";
const LAUNCH_PROJECT = process.env.MP_AUTOMATOR_LAUNCH === "1";
const PROJECT_PATH = "D:/property-ai/tmp/delivery-check-miniprogram-20260928";
const CLI_PATH = "D:/微信web开发者工具/cli.bat";
const API_ORIGIN = "http://127.0.0.1:8093";
const API_BASE = `${API_ORIGIN}/api/v1`;
const OUT = __dirname;
const requests = [];

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function tolerateTimeout(operation) {
  try {
    return await operation();
  } catch (error) {
    if (!String(error && error.message).includes("timeout waiting for automator response")) {
      throw error;
    }
    await sleep(500);
    return null;
  }
}

async function waitPage(miniProgram, expected) {
  for (let attempt = 0; attempt < 120; attempt += 1) {
    const page = await miniProgram.currentPage();
    if (page && page.path === expected) return page;
    await sleep(250);
  }
  throw new Error(`expected page ${expected}`);
}

async function waitData(page, predicate, label, seconds = 60) {
  for (let attempt = 0; attempt < seconds * 4; attempt += 1) {
    const data = await page.data();
    if (!data.loading && predicate(data)) return data;
    if (attempt > 6 && !data.loading && data.error) {
      throw new Error(`${label}: ${data.error}`);
    }
    await sleep(250);
  }
  throw new Error(`${label} timed out`);
}

async function byText(page, selector, text) {
  for (const element of await page.$$(selector)) {
    if (String((await element.text()) || "").includes(text)) return element;
  }
  throw new Error(`${selector} text missing: ${text}`);
}

async function byAttribute(page, selector, name, value) {
  for (const element of await page.$$(selector)) {
    if ((await element.attribute(name)) === value) return element;
  }
  throw new Error(`${selector} ${name} missing: ${value}`);
}

async function fill(page, selector, placeholder, value) {
  for (const element of await page.$$(selector)) {
    if ((await element.attribute("placeholder")) === placeholder) {
      await element.input(String(value));
      return;
    }
  }
  throw new Error(`input missing: ${placeholder}`);
}

function postJson(url, body) {
  return new Promise((resolve, reject) => {
    const parsed = new URL(url);
    const data = Buffer.from(JSON.stringify(body));
    const request = http.request(
      {
        hostname: parsed.hostname,
        port: parsed.port,
        path: parsed.pathname,
        method: "POST",
        headers: { "Content-Type": "application/json", "Content-Length": data.length },
      },
      (response) => {
        let raw = "";
        response.on("data", (chunk) => { raw += chunk; });
        response.on("end", () => {
          if (response.statusCode !== 200) {
            reject(new Error(`private test inbox returned ${response.statusCode}`));
            return;
          }
          resolve(JSON.parse(raw).data);
        });
      }
    );
    request.on("error", reject);
    request.end(data);
  });
}

async function login(miniProgram) {
  await miniProgram.callWxMethod("clearStorageSync");
  await miniProgram.callWxMethod("reLaunch", { url: "/pages/login/login" });
  const page = await waitPage(miniProgram, "pages/login/login");
  if (PASSWORD) {
    await fill(page, "input", "手机号", PHONE);
    await fill(page, "input", "密码", PASSWORD);
    await tolerateTimeout(() => byText(page, "button", "登录").then((item) => item.tap()));
    const home = await waitPage(miniProgram, "pages/home/home");
    const data = await waitData(home, (value) => value.me && value.me.user_id, "normal home");
    if (!(data.me.roles || []).some((role) => role === "TENANT_ADMIN" || role === "SYSTEM_ADMIN")) {
      throw new Error("normal authenticated user cannot manage knowledge");
    }
    return { home, me: data.me, method: "password" };
  }
  for (const item of await page.$$(".link-text")) {
    if (String(await item.text()).includes("验证码登录")) {
      await tolerateTimeout(() => item.tap());
      break;
    }
  }
  await waitData(page, (data) => data.mode === "sms", "SMS login mode");
  await fill(page, "input", "手机号", PHONE);
  await tolerateTimeout(() => byText(page, "button", "获取验证码").then((item) => item.tap()));
  const challenge = await waitData(page, (data) => data.challengePrompt, "challenge");
  const match = challenge.challengePrompt.match(/(\d+)\s*\+\s*(\d+)\s*=\s*\?/);
  if (!match) throw new Error("challenge format mismatch");
  await fill(page, "input", "挑战答案", Number(match[1]) + Number(match[2]));
  await tolerateTimeout(() => byText(page, "button", "获取验证码").then((item) => item.tap()));
  await waitData(page, (data) => !data.challengePrompt && data.cooldown > 0, "SMS sent");
  const inbox = await postJson(`${API_BASE}/auth/sms/test-inbox`, { phone: PHONE, purpose: "login" });
  await fill(page, "input", "短信验证码", inbox.code);
  await tolerateTimeout(() => byText(page, "button", "登录").then((item) => item.tap()));
  const home = await waitPage(miniProgram, "pages/home/home");
  const data = await waitData(home, (value) => value.me && value.me.user_id, "normal home");
  if (!(data.me.roles || []).some((role) => role === "TENANT_ADMIN" || role === "SYSTEM_ADMIN")) {
    throw new Error("normal authenticated user cannot manage knowledge");
  }
  return { home, me: data.me, method: "sms" };
}

async function navigateFromHome(miniProgram, label, expectedPath) {
  await miniProgram.callWxMethod("reLaunch", { url: "/pages/home/home" });
  const home = await waitPage(miniProgram, "pages/home/home");
  await waitData(home, (data) => data.me && data.me.user_id, "home");
  await tolerateTimeout(() => byText(home, "button", label).then((item) => item.tap()));
  return waitPage(miniProgram, expectedPath);
}

async function selectDocument(page, documentId) {
  for (const element of await page.$$(".document-card")) {
    if (Number(await element.attribute("data-id")) === Number(documentId)) {
      await tolerateTimeout(() => element.tap());
      return waitData(page, (data) => data.selected && Number(data.selected.id) === Number(documentId), "document selection");
    }
  }
  throw new Error("created document is not visible");
}

async function waitIngestion(page, documentId) {
  for (let attempt = 0; attempt < 90; attempt += 1) {
    await tolerateTimeout(() => byText(page, "button", "刷新知识状态").then((item) => item.tap()));
    const data = await waitData(page, (value) => value.items.some((item) => Number(item.id) === Number(documentId)), "knowledge list");
    const document = data.items.find((item) => Number(item.id) === Number(documentId));
    if (document.current_version === 1) return selectDocument(page, documentId);
    if ((document.ingestion_jobs || []).some((job) => job.status === "FAILED")) {
      throw new Error(`ingestion failed: ${JSON.stringify(document.ingestion_jobs)}`);
    }
    await sleep(1000);
  }
  throw new Error("knowledge ingestion timed out");
}

async function ask(miniProgram, propertyId, question) {
  const page = await navigateFromHome(miniProgram, "知识助手", "pages/assistant/assistant");
  const initial = await waitData(page, (data) => data.properties && data.properties.length, "assistant scope");
  const propertyIndex = initial.properties.findIndex((item) => Number(item.id) === Number(propertyId));
  if (propertyIndex < 0) throw new Error("knowledge property is not visible in assistant");
  if (Number(initial.propertyId) !== Number(propertyId)) {
    await (await page.$("picker")).trigger("change", { value: propertyIndex });
  }
  await fill(page, "input", "例如：装修时间有什么规定？", question);
  const startedAt = Date.now();
  await tolerateTimeout(() => byText(page, "button", "提问").then((item) => item.tap()));
  const result = await waitData(page, (data) => data.answerStatus, "knowledge answer", 180);
  return {
    page,
    result,
    latency_ms: Date.now() - startedAt,
  };
}

async function main() {
  if (!PHONE || !RUN_ID) throw new Error("private runtime inputs are missing");
  const miniProgram = LAUNCH_PROJECT
    ? await automator.launch({ projectPath: PROJECT_PATH, cliPath: CLI_PATH })
    : await automator.connect({ wsEndpoint: WS });
  let requestLoggerInstalled = false;
  try {
    await miniProgram.exposeFunction("recordDeliveryRagRequest", (entry) => requests.push(entry));
    await miniProgram.mockWxMethod("request", function recordRequest(options) {
      const self = this;
      // This callback is serialized into the mini-program runtime and cannot
      // close over Node-side constants.
      if (!options.url.startsWith("http://127.0.0.1:8093/api/v1/")) {
        throw new Error("request left the delivery API");
      }
      if (options.url.includes("/work-orders")) throw new Error("work-order requests are outside this RAG run");
      return new Promise((resolve, reject) => self.origin({
        ...options,
        success(response) {
          recordDeliveryRagRequest({
            method: options.method || "GET",
            path: new URL(options.url).pathname,
            status: response.statusCode,
            request_id: response.data && response.data.request_id,
            received_at_utc: new Date().toISOString(),
          });
          if (options.success) options.success(response);
          resolve(response);
        },
        fail(error) {
          recordDeliveryRagRequest({
            method: options.method || "GET",
            path: new URL(options.url).pathname,
            error: String(error && error.errMsg ? error.errMsg : error),
            received_at_utc: new Date().toISOString(),
          });
          if (options.fail) options.fail(error);
          reject(error);
        },
      }));
    });
    requestLoggerInstalled = true;

    const session = await login(miniProgram);
    let page = await navigateFromHome(miniProgram, "知识库维护", "pages/supervisor/knowledge");
    const scope = await waitData(page, (data) => data.propertyId && data.properties.length, "knowledge management scope");
    const propertyId = Number(scope.propertyId);
    if (MODE === "preflight") {
      await sleep(1000);
      const current = await miniProgram.currentPage();
      if (!current || current.path !== "pages/supervisor/knowledge") {
        throw new Error("knowledge-management page was not active at screenshot time");
      }
      const screenshot = path.join(OUT, `${RUN_ID}-00-rag-page-preflight.png`);
      await tolerateTimeout(() => miniProgram.screenshot({ path: screenshot }));
      const evidence = {
        task_id: "REAL-AI-INTEGRATION-027",
        run_id: RUN_ID,
        received_at_utc: new Date().toISOString(),
        result: "PAGE_READY_NO_MODEL_CALL",
        interaction: session.method === "password"
          ? "normal password page login and actual knowledge-management navigation"
          : "SMS page login with isolated Development test inbox and actual knowledge-management navigation",
        api_origin: API_ORIGIN,
        user_id: session.me.user_id,
        roles: session.me.roles,
        property_id: propertyId,
        visible_property_count: scope.properties.length,
        phone_sha256_16: crypto.createHash("sha256").update(PHONE).digest("hex").slice(0, 16),
        screenshot: path.basename(screenshot),
        requests,
        model_calls: 0,
      };
      fs.writeFileSync(
        path.join(OUT, `${RUN_ID}-page-preflight.json`),
        JSON.stringify(evidence, null, 2),
        "utf8"
      );
      process.stdout.write(JSON.stringify({
        run_id: RUN_ID,
        result: evidence.result,
        user_id: evidence.user_id,
        property_id: propertyId,
        visible_property_count: evidence.visible_property_count,
      }));
      return;
    }
    if (MODE !== "full") throw new Error(`unsupported mode ${MODE}`);
    const title = `${RUN_ID} 消防通道纸箱处置规则`;
    const sourceText = "本合成小区消防通道必须保持畅通。发现纸箱堆放时，应联系物业服务中心，由工作人员安排清理；住户不要自行搬动带电设备。";
    await fill(page, "input", "文档标题", title);
    await fill(page, "textarea", "粘贴本版本知识正文", sourceText);
    await tolerateTimeout(() => byText(page, "button", "建立并提交导入").then((item) => item.tap()));
    const created = await waitData(page, (data) => data.selected && data.notice.includes("已建立"), "document create");
    const documentId = Number(created.selected.id);
    const active = await waitIngestion(page, documentId);
    const activeScreenshot = path.join(OUT, `${RUN_ID}-01-document-active.png`);
    await tolerateTimeout(() => miniProgram.screenshot({ path: activeScreenshot }));

    const question = "消防通道发现纸箱堆放时应该怎么办？";
    const answered = await ask(miniProgram, propertyId, question);
    const citation = (answered.result.citations || []).find(
      (item) => Number(item.document_id) === documentId && Number(item.version) === 1
    );
    if (!citation) throw new Error("answer did not cite the created active document version");
    const answerScreenshot = path.join(OUT, `${RUN_ID}-02-answer-with-citation.png`);
    await tolerateTimeout(() => miniProgram.screenshot({ path: answerScreenshot }));

    page = await navigateFromHome(miniProgram, "知识库维护", "pages/supervisor/knowledge");
    await waitData(page, (data) => data.items.some((item) => Number(item.id) === documentId), "document list before archive");
    await selectDocument(page, documentId);
    await tolerateTimeout(() => byText(page, "button", "归档文档").then((item) => item.tap()));
    await waitData(page, (data) => data.notice.includes("已归档"), "archive");
    const archivedScreenshot = path.join(OUT, `${RUN_ID}-03-document-archived.png`);
    await tolerateTimeout(() => miniProgram.screenshot({ path: archivedScreenshot }));

    const afterArchive = await ask(miniProgram, propertyId, question);
    if ((afterArchive.result.citations || []).some((item) => Number(item.document_id) === documentId)) {
      throw new Error("archived document remained retrievable");
    }
    const archiveQueryScreenshot = path.join(OUT, `${RUN_ID}-04-archive-query.png`);
    await tolerateTimeout(() => miniProgram.screenshot({ path: archiveQueryScreenshot }));

    const evidence = {
      task_id: "REAL-AI-INTEGRATION-027",
      run_id: RUN_ID,
      received_at_utc: new Date().toISOString(),
      interaction: "actual WeChat Developer Tools page controls and origin wx.request",
      api_origin: API_ORIGIN,
      login: {
        method: session.method === "password"
          ? "normal password page login"
          : "SMS page login with isolated Development test inbox",
        user_id: session.me.user_id,
        roles: session.me.roles,
        phone_sha256_16: crypto.createHash("sha256").update(PHONE).digest("hex").slice(0, 16),
      },
      property_id: propertyId,
      document: {
        id: documentId,
        version: 1,
        title,
        status_before_archive: active.selected.status,
        ingestion_jobs: active.selected.ingestion_jobs,
        status_after_archive: "ARCHIVED",
      },
      answer: {
        question,
        status: answered.result.answerStatus,
        text: answered.result.answer,
        citations: answered.result.citations,
        latency_ms: answered.latency_ms,
      },
      after_archive: {
        status: afterArchive.result.answerStatus,
        text: afterArchive.result.answer,
        citations: afterArchive.result.citations,
        latency_ms: afterArchive.latency_ms,
      },
      screenshots: [
        path.basename(activeScreenshot),
        path.basename(answerScreenshot),
        path.basename(archivedScreenshot),
        path.basename(archiveQueryScreenshot),
      ],
      requests,
      boundaries: {
        normal_page_controls: true,
        database_business_writes: false,
        response_mocked: false,
        work_order_requests_blocked: true,
        development_sms_only_for_local_login: true,
      },
    };
    fs.writeFileSync(
      path.join(OUT, `${RUN_ID}-page-evidence.json`),
      JSON.stringify(evidence, null, 2),
      "utf8"
    );
    process.stdout.write(JSON.stringify({
      run_id: RUN_ID,
      document_id: documentId,
      property_id: propertyId,
      answer_status: evidence.answer.status,
      citation_count: evidence.answer.citations.length,
      after_archive_status: evidence.after_archive.status,
      after_archive_citation_count: evidence.after_archive.citations.length,
    }));
  } finally {
    if (requestLoggerInstalled) await miniProgram.restoreWxMethod("request").catch(() => {});
    await miniProgram.disconnect();
  }
}

main().catch((error) => {
  const redacted = String(error && error.stack ? error.stack : error).split(PHONE || "").join("[PRIVATE]");
  fs.writeFileSync(
    path.join(OUT, `${RUN_ID || "unknown"}-page-error.txt`),
    `${redacted}\n${JSON.stringify({ requests }, null, 2)}`,
    "utf8"
  );
  process.exitCode = 1;
});
