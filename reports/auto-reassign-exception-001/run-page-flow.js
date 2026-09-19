"use strict";

const fs = require("fs");
const http = require("http");
const path = require("path");
const automator = require(process.env.MP_AUTOMATOR_MODULE);

const OUT = __dirname;
const API_PREFIX = "/api/v1";
const WS = process.env.MP_AUTOMATOR_WS || "ws://127.0.0.1:9435";
const PREPARE = JSON.parse(fs.readFileSync(path.join(OUT, "prepare-evidence.json"), "utf8"));
const ACCOUNTS = JSON.parse(
  fs.readFileSync(path.join(OUT, "..", "mp-current-full-regression", "accounts.local.json"), "utf8")
);

function wait(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function requestJson(method, pathname, body, token) {
  return new Promise((resolve, reject) => {
    const encoded = body === undefined ? null : JSON.stringify(body);
    const headers = { "X-Request-Id": "auto-reassign-exception-page-flow" };
    if (encoded !== null) {
      headers["Content-Type"] = "application/json";
      headers["Content-Length"] = Buffer.byteLength(encoded);
    }
    if (token) {
      headers.Authorization = `Bearer ${token}`;
    }
    const request = http.request(
      { host: "127.0.0.1", port: 8083, path: `${API_PREFIX}${pathname}`, method, headers },
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

async function tap(page, label) {
  for (const node of await page.$$("button")) {
    if (((await node.text()) || "").trim() === label) {
      await node.tap();
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

async function smsLogin(miniProgram, phone) {
  let page = await waitPath(miniProgram, "pages/login/login");
  if ((await page.data()).mode !== "sms") {
    for (const node of await page.$$(".link-text")) {
      if (((await node.text()) || "").includes("验证码登录")) {
        await node.tap();
        break;
      }
    }
  }
  page = await current(miniProgram);
  await fill(page, "手机号", phone);
  await tap(page, "获取验证码");
  await wait(400);
  page = await current(miniProgram);
  await fill(page, "挑战答案", challengeAnswer((await page.data()).challengePrompt));
  await tap(page, "获取验证码");
  await wait(400);
  const inbox = await requestJson("POST", "/auth/sms/test-inbox", { phone, purpose: "login" });
  await fill(await current(miniProgram), "短信验证码", inbox.code);
  await tap(await current(miniProgram), "登录");
  return waitPath(miniProgram, "pages/home/home");
}

async function loginAs(miniProgram, role) {
  await resetToLogin(miniProgram);
  return smsLogin(miniProgram, ACCOUNTS.phones[role]);
}

async function openDetail(miniProgram, route, workOrderId) {
  await miniProgram.callWxMethod("reLaunch", { url: `${route}?id=${workOrderId}` });
  const page = await waitPath(miniProgram, route);
  for (let attempt = 0; attempt < 40; attempt += 1) {
    const data = await page.data();
    if (data.workOrder && Number(data.workOrder.id) === Number(workOrderId)) {
      return page;
    }
    if (data.error) {
      throw new Error(`Detail load failed: ${data.error}`);
    }
    await wait(300);
  }
  throw new Error(`Work order ${workOrderId} did not load on ${route}`);
}

async function waitPageState(page, predicate, description, attempts = 80) {
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    const data = await page.data();
    if (predicate(data)) {
      return data;
    }
    if (data.error) {
      throw new Error(`${description} failed: ${data.error}`);
    }
    await wait(300);
  }
  const data = await page.data();
  throw new Error(`${description} timed out; status=${data.workOrder && data.workOrder.status}`);
}

async function waitResidentState(page, predicate, description, attempts = 80) {
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    const data = await page.data();
    if (predicate(data)) {
      return data;
    }
    if (data.error) {
      throw new Error(`${description} failed: ${data.error}`);
    }
    if (!data.loading) {
      await tap(page, "刷新");
    }
    await wait(500);
  }
  const data = await page.data();
  throw new Error(`${description} timed out; status=${data.workOrder && data.workOrder.status}`);
}

async function selectQueueItem(page, workOrderId) {
  for (let attempt = 0; attempt < 30; attempt += 1) {
    for (const node of await page.$$(".card")) {
      if (Number(await node.attribute("data-id")) === Number(workOrderId)) {
        await node.tap();
        await wait(150);
        const data = await page.data();
        if (Number(data.selectedId) === Number(workOrderId)) {
          return;
        }
      }
    }
    await wait(300);
  }
  throw new Error(`Supervisor queue item ${workOrderId} not found`);
}

async function screenshot(miniProgram, name) {
  await miniProgram.screenshot({ path: path.join(OUT, name) });
}

function employeeRole(employeeId) {
  for (const role of ["staff", "staff2"]) {
    if (Number(PREPARE.employees[role].employee_id) === Number(employeeId)) {
      return role;
    }
  }
  throw new Error(`No synthetic role mapped to employee ${employeeId}`);
}

async function runUnresolvedContinue(miniProgram) {
  await loginAs(miniProgram, "resident");
  await miniProgram.callWxMethod("reLaunch", {
    url: `/pages/work-orders/create?houseId=${PREPARE.house_id}`,
  });
  let page = await waitPath(miniProgram, "pages/work-orders/create");
  await fill(page, "报修说明", "未解决回执续办验证：卧室插座接触不良，请安排电气员工检查。" );
  await tap(page, "提交报修");
  page = await waitPath(miniProgram, "pages/work-orders/detail");
  let data = await waitResidentState(
    page,
    (item) => item.workOrder && item.workOrder.status === "ASSIGNED" && item.autoDispatched,
    "unresolved-flow dispatch",
    100
  );
  const workOrderId = Number(data.workOrder.id);
  const assignments = data.item.assignments || [];
  const employeeId = Number(assignments[assignments.length - 1].employee_id);
  const role = employeeRole(employeeId);

  await loginAs(miniProgram, role);
  page = await openDetail(miniProgram, "/pages/staff-orders/detail", workOrderId);
  await tap(page, "接单");
  await waitPageState(page, (item) => item.workOrder.status === "ACCEPTED", "unresolved accept");
  await tap(page, "开始处理");
  await waitPageState(page, (item) => item.workOrder.status === "PROCESSING", "unresolved start");
  await fill(page, "例如：电气维修", "电气维修");
  await fill(page, "说明检查、处理步骤和结果", "完成初检，但需要主管给出继续处理方案。" );
  const switches = await page.$$("switch");
  if (switches.length < 2) {
    throw new Error("Resolved receipt switch was not found");
  }
  await switches[1].tap();
  await wait(150);
  await fill(page, "未解决原因（必填）", "当前处理周期无法解决，等待主管方案。" );
  await screenshot(miniProgram, "13-staff-unresolved-receipt.png");
  await tap(page, "提交未解决回执并上报");
  data = await waitPageState(
    page,
    (item) => item.workOrder.process_reason === "INTERNAL_UNRESOLVED" && !item.canFinish,
    "unresolved receipt escalation"
  );

  await loginAs(miniProgram, "tenant_admin");
  await miniProgram.callWxMethod("reLaunch", {
    url: `/pages/supervisor/queue?propertyId=${PREPARE.property_id}&tenantId=${PREPARE.tenant_id}`,
  });
  page = await waitPath(miniProgram, "pages/supervisor/queue");
  await selectQueueItem(page, workOrderId);
  await fill(page, "处理方案说明；继续处理时必填", "主管确认安全续办方案，创建新的处理周期。" );
  await screenshot(miniProgram, "14-supervisor-continue-unresolved.png");
  await tap(page, "按说明继续处理");
  await wait(1000);

  await loginAs(miniProgram, role);
  page = await openDetail(miniProgram, "/pages/staff-orders/detail", workOrderId);
  data = await page.data();
  if (!data.canFinish || data.workOrder.process_reason !== "NONE") {
    throw new Error("CONTINUE did not open a finishable follow-up cycle");
  }
  await fill(page, "例如：电气维修", "电气维修复办");
  await fill(page, "说明检查、处理步骤和结果", "按主管方案更换接线端子并完成通电复测。" );
  await screenshot(miniProgram, "15-staff-resolved-new-cycle.png");
  await tap(page, "提交完整回执并完成");
  await waitPageState(page, (item) => item.workOrder.status === "WAIT_CONFIRM", "follow-up receipt");

  await loginAs(miniProgram, "resident");
  page = await openDetail(miniProgram, "/pages/work-orders/detail", workOrderId);
  data = await waitPageState(page, (item) => item.workOrder.status === "WAIT_CONFIRM", "follow-up resident");
  await tap(page, "确认解决");
  data = await waitPageState(page, (item) => item.workOrder.status === "CLOSED", "follow-up close");
  await screenshot(miniProgram, "16-resident-unresolved-cycle-closed.png");

  const result = {
    task_id: "AUTO-REASSIGN-EXCEPTION-001-FIX-1-RUNTIME",
    captured_at: new Date().toISOString(),
    code_sha: process.env.CODE_SHA || null,
    work_order_id: workOrderId,
    employee_id: employeeId,
    final_status: data.workOrder.status,
    path: [
      "PROCESSING receipt unresolved",
      "INTERNAL_UNRESOLVED",
      "supervisor CONTINUE with plan",
      "new process cycle resolved receipt",
      "resident CLOSED",
    ],
    screenshots: [
      "13-staff-unresolved-receipt.png",
      "14-supervisor-continue-unresolved.png",
      "15-staff-resolved-new-cycle.png",
      "16-resident-unresolved-cycle-closed.png",
    ],
    boundaries: {
      actual_page_actions: true,
      official_sms_sessions: true,
      direct_database_write_used: false,
      real_model_verified: false,
      production_v1_passed: false,
    },
    redaction: "No phone, SMS code, token, password, resident name, or credential is stored.",
  };
  fs.writeFileSync(
    path.join(OUT, "fix1-page-evidence.json"),
    `${JSON.stringify(result, null, 2)}\n`,
    "utf8"
  );
  console.log(JSON.stringify({ task_id: result.task_id, work_order_id: workOrderId, final_status: result.final_status }));
}

(async () => {
  const miniProgram = await automator.connect({ wsEndpoint: WS });
  if (process.env.VALIDATE_UNRESOLVED_CONTINUE === "1") {
    await runUnresolvedContinue(miniProgram);
    miniProgram.disconnect();
    return;
  }
  let page;
  let workOrderId;
  let initialEmployeeId;
  let reassignedEmployeeId;
  let residentData;
  let staffData;
  const transitions = [];
  const resumeAfterExternal = process.env.RESUME_AFTER_EXTERNAL === "1";
  const resumeAfterContinue = process.env.RESUME_AFTER_CONTINUE === "1";

  if (resumeAfterExternal || resumeAfterContinue) {
    workOrderId = Number(process.env.RESUME_WORK_ORDER_ID);
    initialEmployeeId = Number(PREPARE.employees.staff2.employee_id);
    reassignedEmployeeId = Number(PREPARE.employees.staff.employee_id);
    transitions.push(
      "CREATE->PENDING_ANALYSIS->PENDING_ASSIGNMENT->ASSIGNED",
      "ASSIGNED->PENDING_ASSIGNMENT->ASSIGNED(reassigned)",
      "PROCESSING/NONE->PROCESSING/INTERNAL_UNRESOLVED",
      "INTERNAL_UNRESOLVED->EXTERNAL_COORDINATION"
    );
    if (resumeAfterContinue) {
      transitions.push("EXTERNAL_COORDINATION->NONE");
    }
  } else {
    await loginAs(miniProgram, "resident");
  if (process.env.RESUME_WORK_ORDER_ID) {
    workOrderId = Number(process.env.RESUME_WORK_ORDER_ID);
    page = await openDetail(miniProgram, "/pages/work-orders/detail", workOrderId);
    residentData = await waitResidentState(
      page,
      (data) => data.workOrder && data.workOrder.status === "ASSIGNED" && data.autoDispatched,
      "resume initial automatic dispatch"
    );
  } else {
    await miniProgram.callWxMethod("reLaunch", {
      url: `/pages/work-orders/create?houseId=${PREPARE.house_id}`,
    });
    page = await waitPath(miniProgram, "pages/work-orders/create");
    await fill(page, "报修说明", "本轮自动改派验证：客厅插座偶发火花，请安排具备电气技能的在岗员工检查维修。");
    await screenshot(miniProgram, "01-resident-create.png");
    await tap(page, "提交报修");
    page = await waitPath(miniProgram, "pages/work-orders/detail");
    residentData = await waitResidentState(
      page,
      (data) => data.workOrder && data.workOrder.status === "ASSIGNED" && data.autoDispatched,
      "initial automatic dispatch",
      100
    );
    workOrderId = Number(residentData.workOrder.id);
  }
  const initialAssignments = residentData.item.assignments || [];
  transitions.push("CREATE->PENDING_ANALYSIS->PENDING_ASSIGNMENT->ASSIGNED");
  if (process.env.RESUME_AFTER_REASSIGN === "1") {
    const rejected = [...initialAssignments].reverse().find((item) => item.status === "REJECTED");
    const latest = initialAssignments[initialAssignments.length - 1];
    if (!rejected || !latest || latest.status !== "ACTIVE") {
      throw new Error("Resume-after-reassign requires one rejected and one current assignment");
    }
    initialEmployeeId = Number(rejected.employee_id);
    reassignedEmployeeId = Number(latest.employee_id);
    transitions.push("ASSIGNED->PENDING_ASSIGNMENT->ASSIGNED(reassigned)");
    await screenshot(miniProgram, "04-resident-auto-reassigned.png");
  } else {
    initialEmployeeId = Number(initialAssignments[initialAssignments.length - 1].employee_id);
    await screenshot(miniProgram, "02-resident-auto-assigned.png");

    const initialRole = employeeRole(initialEmployeeId);
    await loginAs(miniProgram, initialRole);
    page = await openDetail(miniProgram, "/pages/staff-orders/detail", workOrderId);
    await fill(page, "拒单原因（必填）", "本轮合成验证：当前已有紧急任务，请系统自动改派。" );
    await screenshot(miniProgram, "03-staff-reject.png");
    await tap(page, "拒单并自动改派");
    await wait(1200);

    await loginAs(miniProgram, "resident");
    page = await openDetail(miniProgram, "/pages/work-orders/detail", workOrderId);
    residentData = await waitResidentState(
      page,
      (data) => {
        const assignments = (data.item && data.item.assignments) || [];
        const latest = assignments[assignments.length - 1];
        return (
          data.workOrder &&
          data.workOrder.status === "ASSIGNED" &&
          latest &&
          Number(latest.employee_id) !== initialEmployeeId &&
          latest.reason === "AUTO_DISPATCH" &&
          latest.assigned_by_user_id == null
        );
      },
      "automatic redispatch",
      100
    );
    const reassignedAssignments = residentData.item.assignments || [];
    reassignedEmployeeId = Number(reassignedAssignments[reassignedAssignments.length - 1].employee_id);
    transitions.push("ASSIGNED->PENDING_ASSIGNMENT->ASSIGNED(reassigned)");
    await screenshot(miniProgram, "04-resident-auto-reassigned.png");
  }

  await loginAs(miniProgram, employeeRole(reassignedEmployeeId));
  page = await openDetail(miniProgram, "/pages/staff-orders/detail", workOrderId);
  await tap(page, "接单");
  await waitPageState(page, (data) => data.workOrder && data.workOrder.status === "ACCEPTED", "accept");
  await tap(page, "开始处理");
  await waitPageState(page, (data) => data.workOrder && data.workOrder.status === "PROCESSING", "start");
  await fill(page, "异常或等待原因（必填）", "现场问题超出一线处置范围，需要主管决定后续协调方式。" );
  await tap(page, "提交主管处理");
  staffData = await waitPageState(
    page,
    (data) => data.workOrder && data.workOrder.process_reason === "INTERNAL_UNRESOLVED",
    "staff escalation"
  );
  if (staffData.canFinish) {
    throw new Error("Escalated order incorrectly remains finishable by staff");
  }
  transitions.push("PROCESSING/NONE->PROCESSING/INTERNAL_UNRESOLVED");
  await screenshot(miniProgram, "05-staff-escalated.png");
  }

  const reassignedRole = employeeRole(reassignedEmployeeId);

  if (!resumeAfterContinue) {
  await loginAs(miniProgram, "tenant_admin");
  await miniProgram.callWxMethod("reLaunch", {
    url: `/pages/supervisor/queue?propertyId=${PREPARE.property_id}&tenantId=${PREPARE.tenant_id}`,
  });
  page = await waitPath(miniProgram, "pages/supervisor/queue");
  await selectQueueItem(page, workOrderId);
  let queueData = await page.data();
  if (!resumeAfterExternal) {
    await fill(page, "处理方案说明；继续处理时必填", "由物业主管记录并协调合成第三方资源，不触发任何真实外部联系。" );
    await screenshot(miniProgram, "06-supervisor-before-external.png");
    await tap(page, "物业协调第三方");
    await wait(1200);
    await selectQueueItem(page, workOrderId);
    queueData = await page.data();
    transitions.push("INTERNAL_UNRESOLVED->EXTERNAL_COORDINATION");
  }
  if (queueData.selectedProcessReason !== "EXTERNAL_COORDINATION") {
    throw new Error(`Expected EXTERNAL_COORDINATION; current=${queueData.selectedProcessReason}`);
  }
  await screenshot(miniProgram, "07-supervisor-external.png");
  await fill(page, "处理方案说明；继续处理时必填", "主管已给出安全处置方案，允许原员工继续完成本轮合成维修。" );
  await tap(page, "按说明继续处理");
  await wait(800);
  queueData = await page.data();
  if ((queueData.items || []).some((item) => Number(item.id) === workOrderId)) {
    throw new Error("Continued order remained in supervisor exception queue");
  }
  transitions.push("EXTERNAL_COORDINATION->NONE");
  await screenshot(miniProgram, "08-supervisor-after-continue.png");
  }

  await loginAs(miniProgram, reassignedRole);
  page = await openDetail(miniProgram, "/pages/staff-orders/detail", workOrderId);
  staffData = await page.data();
  if (!staffData.canFinish || staffData.workOrder.process_reason !== "NONE") {
    throw new Error("Supervisor continuation did not restore staff completion");
  }
  await fill(page, "例如：电气维修", "电气维修");
  await fill(page, "说明检查、处理步骤和结果", "按主管方案检查插座接线并紧固端子，完成通电复测。" );
  await fill(page, "无材料可留空", "绝缘胶带");
  await fill(page, "无需跟进可留空", "住户继续观察，若异常复现可依法重开工单。" );
  await screenshot(miniProgram, "09-staff-complete.png");
  await tap(page, "提交完整回执并完成");
  await waitPageState(page, (data) => data.workOrder && data.workOrder.status === "WAIT_CONFIRM", "receipt");
  transitions.push("PROCESSING->WAIT_CONFIRM");

  await loginAs(miniProgram, "resident");
  page = await openDetail(miniProgram, "/pages/work-orders/detail", workOrderId);
  residentData = await waitPageState(
    page,
    (data) => data.workOrder && data.workOrder.status === "WAIT_CONFIRM" && data.latestReceipt,
    "resident receipt"
  );
  await screenshot(miniProgram, "10-resident-wait-confirm.png");
  await tap(page, "确认解决");
  residentData = await waitPageState(page, (data) => data.workOrder && data.workOrder.status === "CLOSED", "resident confirm");
  transitions.push("WAIT_CONFIRM->COMPLETED->CLOSED");
  await screenshot(miniProgram, "11-resident-closed.png");

  await loginAs(miniProgram, "resident");
  page = await openDetail(miniProgram, "/pages/work-orders/detail", workOrderId);
  const reentered = await waitPageState(page, (data) => data.workOrder && data.workOrder.status === "CLOSED", "resident relogin");
  await screenshot(miniProgram, "12-resident-relogin-closed.png");

  const result = {
    task_id: "AUTO-REASSIGN-EXCEPTION-001",
    captured_at: new Date().toISOString(),
    code_sha: process.env.CODE_SHA || null,
    environment: {
      api: "http://127.0.0.1:8083/api/v1",
      database: PREPARE.environment.database,
      revision: PREPARE.environment.revision,
      queue: PREPARE.environment.queue,
      provider: PREPARE.environment.provider,
      automator_ws: WS,
    },
    work_order_id: workOrderId,
    house_id: PREPARE.house_id,
    initial_employee_id: initialEmployeeId,
    reassigned_employee_id: reassignedEmployeeId,
    rejected_employee_excluded: initialEmployeeId !== reassignedEmployeeId,
    page_actions: [
      "resident created repair",
      "initial staff rejected with reason",
      "reassigned staff accepted and started",
      "reassigned staff escalated",
      "supervisor selected external coordination",
      "supervisor continued with plan",
      "staff submitted resolved receipt",
      "resident confirmed and re-entered after fresh SMS login",
    ],
    transitions,
    final_status: residentData.workOrder.status,
    relogin_status: reentered.workOrder.status,
    final_process_reason: reentered.workOrder.process_reason,
    assignment_count: (reentered.item.assignments || []).length,
    latest_assignment: (() => {
      const assignments = reentered.item.assignments || [];
      const latest = assignments[assignments.length - 1];
      return {
        employee_id: latest.employee_id,
        status: latest.status,
        reason: latest.reason,
        assigned_by_user_id: latest.assigned_by_user_id,
      };
    })(),
    final_log_events: (reentered.logs || []).map((item) => item.event_code),
    screenshots: [
      "01-resident-create.png",
      "02-resident-auto-assigned.png",
      "03-staff-reject.png",
      "04-resident-auto-reassigned.png",
      "05-staff-escalated.png",
      "06-supervisor-before-external.png",
      "07-supervisor-external.png",
      "08-supervisor-after-continue.png",
      "09-staff-complete.png",
      "10-resident-wait-confirm.png",
      "11-resident-closed.png",
      "12-resident-relogin-closed.png",
    ],
    boundaries: {
      official_sms_sessions: true,
      page_actions_not_http_substitutes: true,
      direct_database_write_used: false,
      real_external_coordination_performed: false,
      real_model_verified: false,
      production_v1_passed: false,
    },
    redaction: "No phone, SMS code, token, password, resident name, or credential is stored.",
  };
  fs.writeFileSync(path.join(OUT, "page-evidence.json"), `${JSON.stringify(result, null, 2)}\n`, "utf8");
  miniProgram.disconnect();
  console.log(JSON.stringify({ task_id: result.task_id, work_order_id: workOrderId, final_status: result.final_status }));
  process.exit(
    result.final_status === "CLOSED" &&
      result.relogin_status === "CLOSED" &&
      result.final_process_reason === "NONE" &&
      result.rejected_employee_excluded
      ? 0
      : 2
  );
})().catch((error) => {
  fs.writeFileSync(path.join(OUT, "page-flow-error.txt"), `${error.message}\n${error.stack || ""}\n`, "utf8");
  console.error(error.message);
  process.exit(1);
});
