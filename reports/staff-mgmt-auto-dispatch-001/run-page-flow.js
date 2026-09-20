"use strict";

const fs = require("fs");
const http = require("http");
const path = require("path");
const automator = require(process.env.MP_AUTOMATOR_MODULE);

const OUT = path.resolve(__dirname);
const PRIVATE_CONTEXT = path.resolve(
  __dirname,
  "../../tmp/staff-mgmt-auto-dispatch-001/context.json"
);
const CONTEXT = JSON.parse(fs.readFileSync(PRIVATE_CONTEXT, "utf8"));
const RUN_ID = CONTEXT.run_id;
const WS = process.env.MP_AUTOMATOR_WS || "ws://127.0.0.1:9435";
const requests = [];
const results = [];
const objects = { employees: {}, bind_request_id: null, fill_work_order_id: null, final_work_order_id: null };
const inviteTokens = {};
const RESUME = process.env.STAFF_FLOW_RESUME === "1";
const EVIDENCE_PATH = path.join(OUT, "page-flow-evidence.json");

if (RESUME && fs.existsSync(EVIDENCE_PATH)) {
  const previous = JSON.parse(fs.readFileSync(EVIDENCE_PATH, "utf8"));
  if (previous.run_id !== RUN_ID) {
    throw new Error("resume evidence belongs to another run");
  }
  requests.push(...(previous.request_log || []));
  results.push(...(previous.results || []));
  Object.assign(objects, previous.objects || {});
}

function wait(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function writeJson(name, value) {
  fs.writeFileSync(path.join(OUT, name), `${JSON.stringify(value, null, 2)}\n`, "utf8");
}

function saveProgress() {
  writeJson("page-flow-evidence.json", {
    task_id: "STAFF-MGMT-AUTO-DISPATCH-001",
    run_id: RUN_ID,
    environment: {
      api: "http://127.0.0.1:8083/api/v1",
      database: "127.0.0.1:3307/property_ai_auth001_test",
      revision: "20260919_0009",
      queue: "property_ai.auth001",
      providers: "DEVELOPMENT",
    },
    org: CONTEXT.org,
    objects,
    results,
    request_log: requests.slice(-200),
    boundaries: {
      page_interaction_used: true,
      official_sms_sessions_used: true,
      seed_login_used: false,
      system_role_used: false,
      direct_business_state_database_write_used: false,
      fake_model_only: true,
      real_qwen_verified: false,
      production_v1_passed: false,
    },
    redaction: "No phone, OTP, invite token, access token, refresh token, password, or credential is emitted.",
  });
}

function record(id, status, actual, evidence) {
  const item = { id, status, actual, evidence: evidence || [] };
  const existingIndex = results.findIndex((candidate) => candidate.id === id);
  if (existingIndex >= 0) {
    results[existingIndex] = item;
  } else {
    results.push(item);
  }
  saveProgress();
}

function challengeAnswer(prompt) {
  const match = String(prompt || "").trim().match(/^(\d+)\s*\+\s*(\d+)\s*=\s*\?$/);
  if (!match) {
    throw new Error("development challenge was not recognized");
  }
  return String(Number(match[1]) + Number(match[2]));
}

function postJson(pathname, body, requestId) {
  return new Promise((resolve, reject) => {
    const data = JSON.stringify(body);
    const request = http.request(
      {
        host: "127.0.0.1",
        port: 8083,
        path: `/api/v1${pathname}`,
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Content-Length": Buffer.byteLength(data),
          "X-Request-Id": requestId,
        },
      },
      (response) => {
        let raw = "";
        response.on("data", (chunk) => {
          raw += chunk;
        });
        response.on("end", () => {
          let payload = {};
          try {
            payload = JSON.parse(raw);
          } catch (error) {
            payload = {};
          }
          resolve({ status: response.statusCode, payload });
        });
      }
    );
    request.on("error", reject);
    request.write(data);
    request.end();
  });
}

async function inboxCode(phone) {
  for (let attempt = 0; attempt < 15; attempt += 1) {
    const response = await postJson(
      "/auth/sms/test-inbox",
      { phone, purpose: "login" },
      `${RUN_ID}-inbox-${attempt}`
    );
    const code = response.payload && response.payload.data && response.payload.data.code;
    if (response.status === 200 && code) {
      return code;
    }
    await wait(300);
  }
  throw new Error("development SMS inbox remained empty");
}

async function current(miniProgram) {
  return miniProgram.currentPage();
}

async function waitPath(miniProgram, fragment, timeoutMs) {
  const started = Date.now();
  const limit = timeoutMs || 20000;
  let page;
  while (Date.now() - started < limit) {
    page = await current(miniProgram);
    if (page && page.path && page.path.indexOf(fragment) >= 0) {
      return page;
    }
    await wait(300);
  }
  throw new Error(`page timeout ${fragment}; last=${page && page.path}`);
}

async function waitUntil(check, timeoutMs, label) {
  const started = Date.now();
  const limit = timeoutMs || 20000;
  let last;
  while (Date.now() - started < limit) {
    last = await check();
    if (last) {
      return last;
    }
    await wait(300);
  }
  throw new Error(`condition timeout ${label || "unnamed"}`);
}

async function tapButton(page, label) {
  const buttons = await page.$$("button");
  const labels = [];
  for (const button of buttons) {
    const text = ((await button.text()) || "").replace(/\s+/g, " ").trim();
    labels.push(text);
    if (text === label) {
      await button.tap();
      return;
    }
  }
  throw new Error(`button missing ${label}; have=${labels.join("|")}`);
}

async function tapViewText(page, selector, expected) {
  const nodes = await page.$$(selector);
  for (const node of nodes) {
    const text = ((await node.text()) || "").replace(/\s+/g, " ").trim();
    if (text.indexOf(expected) >= 0) {
      await node.tap();
      return;
    }
  }
  throw new Error(`text target missing ${expected}`);
}

async function fillPlaceholder(page, placeholder, value) {
  for (const input of await page.$$("input")) {
    if ((await input.attribute("placeholder")) === placeholder) {
      await input.input(String(value));
      return;
    }
  }
  for (const area of await page.$$("textarea")) {
    if ((await area.attribute("placeholder")) === placeholder) {
      await area.input(String(value));
      return;
    }
  }
  throw new Error(`input missing ${placeholder}`);
}

async function tapLabel(page, expected) {
  for (const label of await page.$$("label")) {
    const text = ((await label.text()) || "").replace(/\s+/g, " ").trim();
    if (text === expected) {
      await label.tap();
      return;
    }
  }
  throw new Error(`choice missing ${expected}`);
}

async function screenshot(miniProgram, name) {
  try {
    await miniProgram.screenshot({ path: path.join(OUT, name) });
    return name;
  } catch (error) {
    fs.appendFileSync(
      path.join(OUT, "screenshot-errors.log"),
      `${new Date().toISOString()} ${name}: ${error && error.message}\n`,
      "utf8"
    );
    return null;
  }
}

async function attachRequestLogger(miniProgram) {
  await miniProgram.exposeFunction("recordStaffMgmtRequest", (info) => {
    if (!info || !info.url) {
      return;
    }
    requests.push({
      method: info.method || "GET",
      path: String(info.url).replace(/^https?:\/\/[^/]+/, "").split("?")[0],
      status: info.status,
      received_at_local: new Date().toISOString(),
    });
  });
  await miniProgram.mockWxMethod("request", function recordRequest(options) {
    const self = this;
    return new Promise((resolve, reject) => {
      self.origin({
        ...options,
        success(response) {
          if (typeof recordStaffMgmtRequest === "function") {
            recordStaffMgmtRequest({
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
}

async function goFreshLogin(miniProgram) {
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
  if (!page.path || page.path.indexOf("pages/home/home") < 0) {
    await miniProgram.reLaunch("/pages/home/home");
    page = await waitPath(miniProgram, "pages/home/home");
  }
  await wait(500);
  page = await current(miniProgram);
  await tapButton(page, "退出登录");
  return waitPath(miniProgram, "pages/login/login");
}

async function smsLogin(miniProgram, phone) {
  let page = await waitPath(miniProgram, "pages/login/login");
  let data = await page.data();
  if (data.mode !== "sms") {
    await tapViewText(page, ".link-text", "验证码登录");
    await wait(300);
  }
  page = await current(miniProgram);
  await fillPlaceholder(page, "手机号", phone);
  await tapButton(page, "获取验证码");
  page = await waitUntil(
    async () => {
      const candidate = await current(miniProgram);
      const state = await candidate.data();
      return state.challengePrompt ? candidate : null;
    },
    8000,
    "SMS challenge"
  );
  data = await page.data();
  await fillPlaceholder(page, "挑战答案", challengeAnswer(data.challengePrompt));
  const completedSends = requests.filter(
    (item) => item.path === "/api/v1/auth/sms/send" && Number(item.status) === 200
  ).length;
  await tapButton(page, "获取验证码");
  await waitUntil(
    async () =>
      requests.filter(
        (item) => item.path === "/api/v1/auth/sms/send" && Number(item.status) === 200
      ).length > completedSends,
    8000,
    "SMS send completion"
  );
  const code = await inboxCode(phone);
  await fillPlaceholder(page, "短信验证码", code);
  await tapButton(page, "登录");
  page = await waitPath(miniProgram, "pages/home/home", 20000);
  await wait(700);
  return page;
}

async function openStaffPage(miniProgram) {
  let page = await waitPath(miniProgram, "pages/home/home");
  await tapButton(page, "员工管理与状态");
  page = await waitPath(miniProgram, "pages/supervisor/staff");
  await waitUntil(
    async () => {
      const candidate = await current(miniProgram);
      const data = await candidate.data();
      return !data.loading && Number(data.propertyId) === Number(CONTEXT.org.property_id)
        ? candidate
        : null;
    },
    20000,
    "staff page scope"
  );
  return current(miniProgram);
}

async function prepareCatalog(miniProgram) {
  let page = await openStaffPage(miniProgram);
  let data = await page.data();
  if (!(data.duties || []).some((item) => item.code === "MAINTENANCE")) {
    await fillPlaceholder(page, "职责编码", "MAINTENANCE");
    await fillPlaceholder(page, "职责名称", "维修职责");
    await tapButton(page, "新增职责");
    await waitUntil(async () => {
      const state = await (await current(miniProgram)).data();
      return (state.duties || []).some((item) => item.code === "MAINTENANCE");
    }, 10000, "maintenance duty");
  }
  page = await current(miniProgram);
  data = await page.data();
  if (!(data.skills || []).some((item) => item.code === "ELECTRICAL")) {
    await fillPlaceholder(page, "技能编码", "ELECTRICAL");
    await fillPlaceholder(page, "技能名称", "电气技能");
    await tapButton(page, "新增技能");
    await waitUntil(async () => {
      const state = await (await current(miniProgram)).data();
      return (state.skills || []).some((item) => item.code === "ELECTRICAL");
    }, 10000, "electrical skill");
  }
  record("STAFF-CATALOG-PAGE", "PASS", "MAINTENANCE and ELECTRICAL created through manager page");
}

async function createEmployee(miniProgram, specification) {
  await miniProgram.reLaunch("/pages/home/home");
  await waitPath(miniProgram, "pages/home/home");
  let page = await openStaffPage(miniProgram);
  await fillPlaceholder(page, "员工编号", specification.employeeNo);
  await fillPlaceholder(page, "最大容量", specification.capacity);
  const pickers = await page.$$("picker");
  if (pickers.length < 2) {
    throw new Error("department picker was not rendered");
  }
  await pickers[1].trigger("change", { value: 1 });
  await tapLabel(page, "维修职责");
  if (specification.electrical) {
    await tapLabel(page, "电气技能");
  }
  await tapButton(page, "创建并建立当前小区任职");
  page = await waitUntil(async () => {
    const candidate = await current(miniProgram);
    const data = await candidate.data();
    return data.selected && data.selected.employee.employee_no === specification.employeeNo
      ? candidate
      : null;
  }, 15000, `employee ${specification.key}`);
  let data = await page.data();
  objects.employees[specification.key] = {
    employee_id: data.selected.employee.id,
    employee_no: specification.employeeNo,
    max_capacity: Number(specification.capacity),
    electrical: specification.electrical,
  };
  await fillPlaceholder(page, "员工本人手机号", CONTEXT.phones[specification.key]);
  await tapButton(page, "发出邀请");
  page = await waitUntil(async () => {
    const candidate = await current(miniProgram);
    const state = await candidate.data();
    return state.inviteToken ? candidate : null;
  }, 10000, `invite ${specification.key}`);
  data = await page.data();
  inviteTokens[specification.key] = data.inviteToken;
  record(
    `STAFF-CREATE-${specification.key.toUpperCase()}`,
    "PASS",
    `employee_id=${data.selected.employee.id}; department/duty/skill/capacity saved; invite issued`
  );
}

async function reissueEmployeeInvite(miniProgram, key) {
  await miniProgram.reLaunch("/pages/home/home");
  await waitPath(miniProgram, "pages/home/home");
  let page = await openStaffPage(miniProgram);
  const cards = await page.$$(".employee-card");
  let selected = false;
  for (const card of cards) {
    const text = ((await card.text()) || "").replace(/\s+/g, " ");
    if (text.indexOf(objects.employees[key].employee_no) >= 0) {
      await card.tap();
      selected = true;
      break;
    }
  }
  if (!selected) {
    throw new Error(`existing employee missing from scoped page: ${key}`);
  }
  await wait(300);
  page = await current(miniProgram);
  await fillPlaceholder(page, "员工本人手机号", CONTEXT.phones[key]);
  await tapButton(page, "发出邀请");
  page = await waitUntil(async () => {
    const candidate = await current(miniProgram);
    const state = await candidate.data();
    return state.inviteToken ? candidate : null;
  }, 10000, `replacement invite ${key}`);
  const data = await page.data();
  inviteTokens[key] = data.inviteToken;
  record(
    `STAFF-INVITE-${key.toUpperCase()}`,
    "PASS",
    `employee_id=${objects.employees[key].employee_id}; replacement invite issued through manager page`
  );
}

async function claimAndCheckIn(miniProgram, key) {
  await smsLogin(miniProgram, CONTEXT.phones[key]);
  let page = await current(miniProgram);
  await tapButton(page, "认领物业员工邀请");
  page = await waitPath(miniProgram, "pages/employee-claim/employee-claim");
  await fillPlaceholder(page, "邀请令牌", inviteTokens[key]);
  await tapButton(page, "认领");
  page = await waitPath(miniProgram, "pages/home/home", 15000);
  await wait(700);
  await tapButton(page, "今日考勤");
  page = await waitPath(miniProgram, "pages/attendance/attendance");
  await tapButton(page, "签到");
  page = await waitUntil(async () => {
    const candidate = await current(miniProgram);
    const state = await candidate.data();
    return state.snapshot ? candidate : null;
  }, 10000, `attendance ${key}`);
  const data = await page.data();
  objects.employees[key].business_date = data.snapshot.business_date;
  objects.employees[key].on_duty = data.snapshot.daily_status.on_duty;
  const shot = await screenshot(miniProgram, `attendance-${key}.png`);
  record(
    `STAFF-CLAIM-CHECKIN-${key.toUpperCase()}`,
    data.snapshot.daily_status.on_duty === "ON_DUTY" ? "PASS" : "FAIL",
    `employee_id=${objects.employees[key].employee_id}; on_duty=${data.snapshot.daily_status.on_duty}`,
    shot ? [shot] : []
  );
  await logoutToLogin(miniProgram);
}

async function applyResidentBinding(miniProgram) {
  await smsLogin(miniProgram, CONTEXT.phones.resident);
  let page = await current(miniProgram);
  await tapButton(page, "房屋选择 / 绑定");
  page = await waitPath(miniProgram, "pages/houses/houses");
  page = await waitUntil(async () => {
    const candidate = await current(miniProgram);
    const data = await candidate.data();
    return (data.properties || []).some(
      (item) => Number(item.id) === Number(CONTEXT.org.property_id)
    ) ? candidate : null;
  }, 15000, "resident property catalog");
  let data = await page.data();
  const propertyIndex = data.properties.findIndex(
    (item) => Number(item.id) === Number(CONTEXT.org.property_id)
  );
  if (propertyIndex !== Number(data.propertyIndex || 0)) {
    const pickers = await page.$$("picker");
    await pickers[0].trigger("change", { value: propertyIndex });
  }
  page = await waitUntil(async () => {
    const candidate = await current(miniProgram);
    const state = await candidate.data();
    return Number(state.selectedHouseId) === Number(CONTEXT.org.house_id)
      ? candidate
      : null;
  }, 12000, "resident house cascade");
  await tapButton(page, "提交绑定申请");
  page = await waitUntil(async () => {
    const candidate = await current(miniProgram);
    const state = await candidate.data();
    return (state.requests || []).some((item) => item.status === "PENDING")
      ? candidate
      : null;
  }, 12000, "pending binding");
  data = await page.data();
  const pending = data.requests.find((item) => item.status === "PENDING");
  objects.bind_request_id = pending.id;
  const shot = await screenshot(miniProgram, "resident-binding-pending.png");
  record("RESIDENT-BIND-PAGE", "PASS", `bind_request_id=${pending.id}; status=PENDING`, shot ? [shot] : []);
  await logoutToLogin(miniProgram);
}

async function approveResidentBinding(miniProgram) {
  await smsLogin(miniProgram, CONTEXT.phones.tenant_admin);
  let page = await current(miniProgram);
  await tapButton(page, "绑定审核");
  page = await waitPath(miniProgram, "pages/supervisor/bindings");
  page = await waitUntil(async () => {
    const candidate = await current(miniProgram);
    const data = await candidate.data();
    return (data.items || []).some(
      (item) => Number(item.id) === Number(objects.bind_request_id)
    ) ? candidate : null;
  }, 12000, "binding review item");
  const buttons = await page.$$("button");
  let approved = false;
  for (const button of buttons) {
    const text = ((await button.text()) || "").trim();
    const id = await button.attribute("data-id");
    if (text === "通过" && Number(id) === Number(objects.bind_request_id)) {
      await button.tap();
      approved = true;
      break;
    }
  }
  if (!approved) {
    throw new Error("target binding approve button was not found");
  }
  await waitUntil(async () => {
    const state = await (await current(miniProgram)).data();
    return !(state.items || []).some(
      (item) => Number(item.id) === Number(objects.bind_request_id)
    );
  }, 10000, "binding approval refresh");
  const shot = await screenshot(miniProgram, "manager-binding-approved.png");
  record("MANAGER-BIND-APPROVE-PAGE", "PASS", `bind_request_id=${objects.bind_request_id}; approved`, shot ? [shot] : []);
  await logoutToLogin(miniProgram);
}

async function createResidentWorkOrder(miniProgram, kind) {
  await smsLogin(miniProgram, CONTEXT.phones.resident);
  let page = await current(miniProgram);
  await tapButton(page, "房屋选择 / 绑定");
  page = await waitPath(miniProgram, "pages/houses/houses");
  page = await waitUntil(async () => {
    const candidate = await current(miniProgram);
    const data = await candidate.data();
    return (data.items || []).some(
      (item) => Number(item.house_id) === Number(CONTEXT.org.house_id)
    ) ? candidate : null;
  }, 12000, "active resident house");
  const cards = await page.$$(".card");
  let opened = false;
  for (const card of cards) {
    const id = await card.attribute("data-id");
    if (Number(id) === Number(CONTEXT.org.house_id)) {
      await card.tap();
      opened = true;
      break;
    }
  }
  if (!opened) {
    throw new Error("bound house card was not found");
  }
  page = await waitPath(miniProgram, "pages/work-orders/create");
  await fillPlaceholder(
    page,
    "报修说明",
    kind === "fill"
      ? "合成测试：厨房插座冒火花，请安排电工检查（容量占用样本）"
      : "合成测试：客厅插座持续冒火花，请尽快安排电工处理"
  );
  await tapButton(page, "提交报修");
  page = await waitPath(miniProgram, "pages/work-orders/detail", 15000);
  page = await waitUntil(async () => {
    const candidate = await current(miniProgram);
    const data = await candidate.data();
    return data.autoDispatched && data.workOrder && data.workOrder.status === "ASSIGNED"
      ? candidate
      : null;
  }, 35000, `automatic dispatch ${kind}`);
  const data = await page.data();
  const assignment = data.item.assignments[data.item.assignments.length - 1];
  const id = Number(data.workOrder.id);
  if (kind === "fill") {
    objects.fill_work_order_id = id;
  } else {
    objects.final_work_order_id = id;
    objects.final_assignment = {
      employee_id: assignment.employee_id,
      assigned_by_user_id_is_null: assignment.assigned_by_user_id == null,
      reason: assignment.reason,
      status: assignment.status,
    };
  }
  const shot = await screenshot(miniProgram, `resident-${kind}-auto-assigned.png`);
  record(
    `RESIDENT-${kind.toUpperCase()}-AUTO-DISPATCH-PAGE`,
    "PASS",
    `work_order_id=${id}; employee_id=${assignment.employee_id}; AUTO_DISPATCH visible`,
    shot ? [shot] : []
  );
  if (kind === "fill") {
    await logoutToLogin(miniProgram);
  }
}

async function endEmployeeAssignment(miniProgram, key) {
  await smsLogin(miniProgram, CONTEXT.phones.tenant_admin);
  let page = await openStaffPage(miniProgram);
  const cards = await page.$$(".employee-card");
  let selected = false;
  for (const card of cards) {
    const text = ((await card.text()) || "").replace(/\s+/g, " ");
    if (text.indexOf(objects.employees[key].employee_no) >= 0) {
      await card.tap();
      selected = true;
      break;
    }
  }
  if (!selected) {
    throw new Error("ended candidate was not visible to manager");
  }
  await wait(300);
  page = await current(miniProgram);
  await tapButton(page, "结束任职");
  await waitUntil(async () => {
    const state = await (await current(miniProgram)).data();
    return !(state.items || []).some(
      (item) => Number(item.employee.id) === Number(objects.employees[key].employee_id)
    );
  }, 10000, "ended assignment removed from current list");
  const shot = await screenshot(miniProgram, "manager-ended-assignment.png");
  record(
    "STAFF-END-ASSIGNMENT-PAGE",
    "PASS",
    `employee_id=${objects.employees[key].employee_id}; current effective assignment ended`,
    shot ? [shot] : []
  );
  await logoutToLogin(miniProgram);
}

(async () => {
  const miniProgram = await automator.connect({ wsEndpoint: WS });
  try {
    await attachRequestLogger(miniProgram);
    await goFreshLogin(miniProgram);
    await smsLogin(miniProgram, CONTEXT.phones.tenant_admin);
    await prepareCatalog(miniProgram);

    const employees = [
      { key: "full", employeeNo: `FULL-${RUN_ID.slice(-8)}`, capacity: 1, electrical: true },
      { key: "mismatch", employeeNo: `MISMATCH-${RUN_ID.slice(-8)}`, capacity: 2, electrical: false },
      { key: "ended", employeeNo: `ENDED-${RUN_ID.slice(-8)}`, capacity: 2, electrical: true },
      { key: "primary", employeeNo: `PRIMARY-${RUN_ID.slice(-8)}`, capacity: 2, electrical: true },
    ];
    const hasExistingEmployees = employees.every(
      (specification) => objects.employees[specification.key]
    );
    if (hasExistingEmployees) {
      for (const specification of employees) {
        await reissueEmployeeInvite(miniProgram, specification.key);
      }
    } else {
      for (const specification of employees) {
        await createEmployee(miniProgram, specification);
      }
    }
    await miniProgram.reLaunch("/pages/home/home");
    await waitPath(miniProgram, "pages/home/home");
    let page = await openStaffPage(miniProgram);
    const listShot = await screenshot(miniProgram, "manager-staff-list.png");
    const listData = await page.data();
    record(
      "STAFF-MANAGEMENT-PAGE",
      Object.keys(objects.employees).every((key) =>
        (listData.items || []).some(
          (item) => Number(item.employee.id) === Number(objects.employees[key].employee_id)
        )
      ) ? "PASS" : "FAIL",
      `current_employee_count=${(listData.items || []).length}; scoped_property_id=${listData.propertyId}`,
      listShot ? [listShot] : []
    );
    await logoutToLogin(miniProgram);

    await claimAndCheckIn(miniProgram, "full");
    await applyResidentBinding(miniProgram);
    await approveResidentBinding(miniProgram);
    await createResidentWorkOrder(miniProgram, "fill");

    await claimAndCheckIn(miniProgram, "mismatch");
    await claimAndCheckIn(miniProgram, "ended");
    await claimAndCheckIn(miniProgram, "primary");
    await endEmployeeAssignment(miniProgram, "ended");
    await createResidentWorkOrder(miniProgram, "final");

    const correctWinner =
      Number(objects.final_assignment.employee_id) ===
      Number(objects.employees.primary.employee_id);
    record(
      "AUTO-DISPATCH-WINNER",
      correctWinner ? "PASS" : "FAIL",
      `expected_primary=${objects.employees.primary.employee_id}; actual=${objects.final_assignment.employee_id}`
    );
    saveProgress();
    console.log(
      JSON.stringify(
        {
          task_id: "STAFF-MGMT-AUTO-DISPATCH-001",
          run_id: RUN_ID,
          final_work_order_id: objects.final_work_order_id,
          result: results.every((item) => item.status === "PASS") ? "PASS" : "FAIL",
          evidence: "reports/staff-mgmt-auto-dispatch-001/page-flow-evidence.json",
        },
        null,
        2
      )
    );
    await miniProgram.disconnect();
    process.exit(results.every((item) => item.status === "PASS") ? 0 : 2);
  } catch (error) {
    fs.writeFileSync(
      path.join(OUT, "page-flow-error.txt"),
      `${error && error.message}\n${error && error.stack}\n`,
      "utf8"
    );
    saveProgress();
    try {
      await miniProgram.disconnect();
    } catch (disconnectError) {
      // Preserve the original failure.
    }
    console.error(error && error.message);
    process.exit(1);
  }
})();
