"use strict";

const fs = require("fs");
const path = require("path");
const automator = require(process.env.MP_AUTOMATOR_MODULE);

const OUT = __dirname;
const WS = process.env.MP_AUTOMATOR_WS || "ws://127.0.0.1:9435";
const CONTEXT = JSON.parse(
  fs.readFileSync(
    path.join(OUT, "..", "..", "tmp", "staff-mgmt-auto-dispatch-001", "context.json"),
    "utf8"
  )
);
const DISPATCH = JSON.parse(
  fs.readFileSync(
    path.join(OUT, "..", "staff-mgmt-auto-dispatch-001", "final-dispatch-evidence.json"),
    "utf8"
  )
);
const STAFF = JSON.parse(
  fs.readFileSync(
    path.join(OUT, "..", "staff-mgmt-auto-dispatch-001", "remediation-database-evidence.json"),
    "utf8"
  )
);
const WORK_ORDER_ID = Number(DISPATCH.work_order.id);
const TARGET_EMPLOYEE_ID = Number(STAFF.employees.find((item) => item.id === 9).id);
const requests = [];

function wait(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

function writeJson(name, value) {
  fs.writeFileSync(path.join(OUT, name), `${JSON.stringify(value, null, 2)}\n`, "utf8");
}

function requestJson(method, pathname, body) {
  const http = require("http");
  return new Promise((resolve, reject) => {
    const encoded = body === undefined ? null : JSON.stringify(body);
    const headers = { "X-Request-Id": "wo-receipt-reassign-page-helper" };
    if (encoded !== null) {
      headers["Content-Type"] = "application/json";
      headers["Content-Length"] = Buffer.byteLength(encoded);
    }
    const request = http.request(
      {
        host: "127.0.0.1",
        port: 8083,
        path: `/api/v1${pathname}`,
        method,
        headers,
      },
      (response) => {
        let raw = "";
        response.on("data", (chunk) => {
          raw += chunk;
        });
        response.on("end", () => {
          const payload = raw ? JSON.parse(raw) : {};
          if (response.statusCode >= 400) {
            reject(new Error(`${method} ${pathname} returned ${response.statusCode}`));
            return;
          }
          resolve(payload.data === undefined ? payload : payload.data);
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
    throw new Error("Unsupported development challenge prompt");
  }
  return String(Number(match[1]) + Number(match[2]));
}

async function current(miniProgram) {
  return miniProgram.currentPage();
}

async function waitPath(miniProgram, expected, attempts = 60) {
  const normalized = String(expected).replace(/^\//, "");
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    const page = await current(miniProgram);
    if (page && page.path && page.path.includes(normalized)) {
      return page;
    }
    await wait(300);
  }
  const page = await current(miniProgram);
  throw new Error(`Expected ${normalized}; current=${page && page.path}`);
}

async function tap(page, label) {
  for (const button of await page.$$("button")) {
    if (((await button.text()) || "").trim() === label) {
      await button.tap();
      return;
    }
  }
  throw new Error(`Button not found: ${label}`);
}

async function fill(page, placeholder, value) {
  const fields = [...(await page.$$("input")), ...(await page.$$("textarea"))];
  for (const field of fields) {
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

async function loginAs(miniProgram, role) {
  let page = await resetToLogin(miniProgram);
  if ((await page.data()).mode !== "sms") {
    for (const node of await page.$$(".link-text")) {
      if (((await node.text()) || "").includes("验证码登录")) {
        await node.tap();
        break;
      }
    }
  }
  page = await current(miniProgram);
  const phone = CONTEXT.phones[role];
  await fill(page, "手机号", phone);
  await tap(page, "获取验证码");
  await wait(400);
  page = await current(miniProgram);
  await fill(page, "挑战答案", challengeAnswer((await page.data()).challengePrompt));
  await tap(page, "获取验证码");
  await wait(400);
  const inbox = await requestJson("POST", "/auth/sms/test-inbox", {
    phone,
    purpose: "login",
  });
  await fill(await current(miniProgram), "短信验证码", inbox.code);
  await tap(await current(miniProgram), "登录");
  return waitPath(miniProgram, "pages/home/home");
}

async function openStaffDetail(miniProgram) {
  await miniProgram.callWxMethod("reLaunch", {
    url: `/pages/staff-orders/detail?id=${WORK_ORDER_ID}`,
  });
  const page = await waitPath(miniProgram, "pages/staff-orders/detail");
  return waitPageData(
    page,
    (data) => data.workOrder && Number(data.workOrder.id) === WORK_ORDER_ID,
    "staff detail"
  );
}

async function waitPageData(page, predicate, description, attempts = 80) {
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    const data = await page.data();
    if (predicate(data)) {
      return { page, data };
    }
    if (data.error) {
      throw new Error(`${description} failed: ${data.error}`);
    }
    await wait(300);
  }
  throw new Error(`${description} timed out`);
}

async function screenshot(miniProgram, name) {
  await miniProgram.screenshot({ path: path.join(OUT, name) });
}

async function selectQueueItem(page) {
  for (let attempt = 0; attempt < 50; attempt += 1) {
    for (const item of await page.$$(".card")) {
      if (Number(await item.attribute("data-id")) === WORK_ORDER_ID) {
        await item.tap();
        return waitPageData(
          page,
          (data) => Number(data.selectedId) === WORK_ORDER_ID,
          "queue selection"
        );
      }
    }
    await wait(300);
  }
  throw new Error(`Work order ${WORK_ORDER_ID} was not visible in supervisor queue`);
}

async function selectEmployee(page) {
  for (const item of await page.$$(".empty")) {
    if (Number(await item.attribute("data-id")) === TARGET_EMPLOYEE_ID) {
      await item.tap();
      return waitPageData(
        page,
        (data) => Number(data.employeeId) === TARGET_EMPLOYEE_ID,
        "employee selection"
      );
    }
  }
  throw new Error(`Employee ${TARGET_EMPLOYEE_ID} was not visible in daily roster`);
}

async function configureInstrumentation(miniProgram) {
  await miniProgram.exposeFunction("recordReceiptRequest", (entry) => {
    if (!entry || !entry.url) {
      return;
    }
    const pathname = String(entry.url).replace(/^https?:\/\/[^/]+/, "");
    if (
      pathname.includes(`/work-orders/${WORK_ORDER_ID}`) ||
      pathname.includes("/files") ||
      pathname.includes("/supervisor/work-orders")
    ) {
      requests.push({ method: entry.method || "GET", path: pathname, status: entry.status });
    }
  });
  await miniProgram.mockWxMethod("request", function (options) {
    const self = this;
    return new Promise((resolve, reject) => {
      self.origin({
        ...options,
        success(response) {
          if (typeof recordReceiptRequest === "function") {
            recordReceiptRequest({
              url: options.url,
              method: options.method,
              status: response.statusCode,
            });
          }
          if (options.success) {
            options.success(response);
          }
          resolve(response);
        },
        fail(error) {
          if (options.fail) {
            options.fail(error);
          }
          reject(error);
        },
      });
    });
  });
  await miniProgram.mockWxMethod("uploadFile", function (options) {
    const self = this;
    return new Promise((resolve, reject) => {
      self.origin({
        ...options,
        success(response) {
          if (typeof recordReceiptRequest === "function") {
            recordReceiptRequest({
              url: options.url,
              method: "UPLOAD",
              status: response.statusCode,
            });
          }
          if (options.success) {
            options.success(response);
          }
          resolve(response);
        },
        fail(error) {
          if (options.fail) {
            options.fail(error);
          }
          reject(error);
        },
      });
    });
  });
  await miniProgram.evaluate(() => {
    const content =
      "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=";
    const filename = `${wx.env.USER_DATA_PATH}/wo-receipt-reassign-002.png`;
    wx.getFileSystemManager().writeFileSync(filename, content, "base64");
  });
  await miniProgram.mockWxMethod("chooseMedia", function (options) {
    const filename = `${wx.env.USER_DATA_PATH}/wo-receipt-reassign-002.png`;
    const size = wx.getFileSystemManager().statSync(filename).size;
    const response = {
      tempFiles: [{ tempFilePath: filename, size }],
      type: "image",
    };
    if (options.success) {
      options.success(response);
    }
    return Promise.resolve(response);
  });
}

async function run() {
  const miniProgram = await automator.connect({ wsEndpoint: WS });
  miniProgram.on("exception", (error) => {
    fs.appendFileSync(
      path.join(OUT, "automator-exception.txt"),
      `${error && error.message}\n${error && error.stack}\n`,
      "utf8"
    );
  });
  try {
    await configureInstrumentation(miniProgram);

    await loginAs(miniProgram, "primary");
    let state = await openStaffDetail(miniProgram);
    if (state.data.workOrder.status === "ASSIGNED") {
      await tap(state.page, "接单");
      state = await waitPageData(
        state.page,
        (data) => data.workOrder && data.workOrder.status === "ACCEPTED",
        "primary accept"
      );
    }
    if (state.data.workOrder.status === "ACCEPTED") {
      await tap(state.page, "开始处理");
      state = await waitPageData(
        state.page,
        (data) => data.workOrder && data.workOrder.status === "PROCESSING",
        "primary start"
      );
    }
    if (
      state.data.workOrder.status !== "PROCESSING" ||
      state.data.workOrder.process_reason !== "NONE"
    ) {
      throw new Error(
        `Unexpected pre-escalation state ${state.data.workOrder.status}/${state.data.workOrder.process_reason}`
      );
    }
    await fill(state.page, "异常或等待原因（必填）", "现场需要改派给具备当前资格的员工继续处理");
    await tap(state.page, "提交主管处理");
    state = await waitPageData(
      state.page,
      (data) =>
        data.workOrder &&
        data.workOrder.status === "PROCESSING" &&
        data.workOrder.process_reason === "INTERNAL_UNRESOLVED",
      "primary escalation"
    );
    await screenshot(miniProgram, "01-primary-escalated.png");

    await loginAs(miniProgram, "tenant_admin");
    await miniProgram.callWxMethod("reLaunch", {
      url: `/pages/supervisor/queue?propertyId=${CONTEXT.org.property_id}&tenantId=${CONTEXT.org.tenant_id}`,
    });
    let page = await waitPath(miniProgram, "pages/supervisor/queue");
    await waitPageData(
      page,
      (data) => data.items.some((item) => Number(item.id) === WORK_ORDER_ID),
      "supervisor queue load"
    );
    await selectQueueItem(page);
    await selectEmployee(page);
    await fill(page, "处理方案说明；继续处理时必填", "原员工上报后由主管改派给本小区在岗合格员工");
    await screenshot(miniProgram, "02-supervisor-reassign-ready.png");
    await tap(page, "改派给已选在岗员工");
    await waitPageData(
      page,
      (data) => !data.items.some((item) => Number(item.id) === WORK_ORDER_ID),
      "supervisor reassign"
    );
    await screenshot(miniProgram, "03-supervisor-after-reassign.png");

    await loginAs(miniProgram, "ended");
    state = await openStaffDetail(miniProgram);
    if (state.data.workOrder.status !== "ASSIGNED") {
      throw new Error(`Target employee saw ${state.data.workOrder.status}, expected ASSIGNED`);
    }
    await screenshot(miniProgram, "04-target-assigned.png");
    await tap(state.page, "接单");
    state = await waitPageData(
      state.page,
      (data) => data.workOrder && data.workOrder.status === "ACCEPTED",
      "target accept"
    );
    await tap(state.page, "开始处理");
    state = await waitPageData(
      state.page,
      (data) => data.workOrder && data.workOrder.status === "PROCESSING",
      "target start"
    );

    await tap(state.page, "添加处理前照片（可选）");
    state = await waitPageData(
      state.page,
      (data) => data.beforeFileIds.length === 1,
      "before image upload"
    );
    await tap(state.page, "添加处理后照片（可选）");
    state = await waitPageData(
      state.page,
      (data) => data.afterFileIds.length === 1,
      "after image upload"
    );
    await fill(state.page, "例如：电气维修", "电气维修");
    await fill(state.page, "说明检查、处理步骤和结果", "检查接线端子并完成紧固复测");
    await fill(state.page, "无材料可留空", "绝缘胶带");
    await screenshot(miniProgram, "05-receipt-media-ready.png");
    await tap(state.page, "提交完整回执并完成");
    state = await waitPageData(
      state.page,
      (data) => data.workOrder && data.workOrder.status === "WAIT_CONFIRM",
      "receipt submit"
    );
    await screenshot(miniProgram, "06-receipt-submitted.png");

    writeJson("page-evidence.json", {
      task_id: "WO-RECEIPT-REASSIGN-002",
      captured_at: new Date().toISOString(),
      code_sha: require("child_process")
        .execFileSync("git", ["rev-parse", "HEAD"], { cwd: path.join(OUT, "..", "..") })
        .toString()
        .trim(),
      environment: {
        api: "http://127.0.0.1:8083/api/v1",
        database: "127.0.0.1:3307/property_ai_auth001_test",
        revision: "20260919_0009",
        queue: "property_ai.auth001",
      },
      work_order_id: WORK_ORDER_ID,
      original_employee_id: Number(DISPATCH.assignment.employee_id),
      target_employee_id: TARGET_EMPLOYEE_ID,
      final_page_status: state.data.workOrder.status,
      page_actions: [
        "original employee accepted and started existing AUTO assignment",
        "original employee escalated through staff page",
        "tenant administrator selected work order and eligible employee on supervisor page",
        "tenant administrator tapped reassign with reason",
        "target employee accepted and started through staff page",
        "target employee tapped before/after photo controls",
        "target employee submitted complete receipt through staff page",
      ],
      requests,
      screenshots: [
        "01-primary-escalated.png",
        "02-supervisor-reassign-ready.png",
        "03-supervisor-after-reassign.png",
        "04-target-assigned.png",
        "05-receipt-media-ready.png",
        "06-receipt-submitted.png",
      ],
      boundaries: {
        actual_page_actions: true,
        actual_http_and_storage: true,
        native_picker_verified: false,
        picker_input: "automator mock of wx.chooseMedia returning a synthetic local PNG",
        direct_database_write_used: false,
        fake_llm_used_for_existing_auto_assignment: true,
        real_model_verified: false,
        production_v1_passed: false,
      },
      redaction:
        "No phone, SMS code, token, password, resident name, file path, or credential is stored.",
    });
  } finally {
    miniProgram.disconnect();
  }
}

run().catch((error) => {
  fs.writeFileSync(
    path.join(OUT, "page-flow-error.txt"),
    `${error && error.message}\n${error && error.stack}\n`,
    "utf8"
  );
  process.exitCode = 1;
});
