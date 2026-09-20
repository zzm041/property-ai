"use strict";

const fs = require("fs");
const http = require("http");
const path = require("path");
const automator = require(process.env.MP_AUTOMATOR_MODULE);

const OUT = path.resolve(__dirname);
const CONTEXT = JSON.parse(
  fs.readFileSync(
    path.resolve(__dirname, "../../tmp/staff-mgmt-auto-dispatch-001/context.json"),
    "utf8"
  )
);
const ORIGINAL = JSON.parse(
  fs.readFileSync(path.join(OUT, "page-flow-evidence.json"), "utf8")
);
const WS = process.env.MP_AUTOMATOR_WS || "ws://127.0.0.1:9435";
const RUN_ID = `${CONTEXT.run_id}-fix1`;
const results = [];
const requests = [];
const objects = {
  primary_employee_id: Number(ORIGINAL.objects.employees.primary.employee_id),
  ended_employee_id: Number(ORIGINAL.objects.employees.ended.employee_id),
  primary_property_id: Number(CONTEXT.org.property_id),
  secondary_property_id: Number(CONTEXT.remediation.secondary_property_id),
};

function wait(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function save() {
  fs.writeFileSync(
    path.join(OUT, "remediation-page-evidence.json"),
    `${JSON.stringify(
      {
        task_id: "STAFF-MGMT-AUTO-DISPATCH-001",
        review_round: 1,
        run_id: RUN_ID,
        environment: {
          api: "http://127.0.0.1:8083/api/v1",
          database: "127.0.0.1:3307/property_ai_auth001_test",
          revision: "20260919_0009",
          head: "8db1d2b47a466380f0d9a8a17c5e9a19871ec7a3",
        },
        objects,
        results,
        request_log: requests.slice(-300),
        boundaries: {
          page_interaction_used: true,
          normal_sms_login_used: true,
          system_role_used: false,
          direct_business_state_database_write_used: false,
          real_model_verified: false,
        },
        redaction:
          "No phone, OTP, invite token, access token, refresh token, password, or credential is emitted.",
      },
      null,
      2
    )}\n`,
    "utf8"
  );
}

function record(id, status, actual, evidence) {
  const item = { id, status, actual, evidence: evidence || [] };
  const index = results.findIndex((candidate) => candidate.id === id);
  if (index >= 0) {
    results[index] = item;
  } else {
    results.push(item);
  }
  save();
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

function challengeAnswer(prompt) {
  const match = String(prompt || "").trim().match(/^(\d+)\s*\+\s*(\d+)\s*=\s*\?$/);
  if (!match) {
    throw new Error("development challenge was not recognized");
  }
  return String(Number(match[1]) + Number(match[2]));
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
  while (Date.now() - started < limit) {
    const page = await current(miniProgram);
    if (page && page.path && page.path.indexOf(fragment) >= 0) {
      return page;
    }
    await wait(250);
  }
  const page = await current(miniProgram);
  throw new Error(`page timeout ${fragment}; last=${page && page.path}`);
}

async function waitUntil(operation, timeoutMs, label) {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    const result = await operation();
    if (result) {
      return result;
    }
    await wait(250);
  }
  throw new Error(`timeout waiting for ${label}`);
}

async function fillPlaceholder(page, placeholder, value) {
  const inputs = [...(await page.$$("input")), ...(await page.$$("textarea"))];
  for (const input of inputs) {
    if ((await input.attribute("placeholder")) === placeholder) {
      await input.input(String(value));
      return;
    }
  }
  throw new Error(`input missing ${placeholder}`);
}

async function tapButton(page, text) {
  const buttons = await page.$$("button");
  for (const button of buttons) {
    if (((await button.text()) || "").trim() === text) {
      await button.tap();
      return;
    }
  }
  throw new Error(`button missing ${text}`);
}

async function tapViewText(page, selector, expected) {
  const nodes = await page.$$(selector);
  for (const node of nodes) {
    if (((await node.text()) || "").trim() === expected) {
      await node.tap();
      return;
    }
  }
  throw new Error(`view missing ${expected}`);
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
  await miniProgram.exposeFunction("recordStaffRemediationRequest", (info) => {
    if (!info || !info.url) {
      return;
    }
    const parsed = new URL(info.url);
    requests.push({
      method: info.method || "GET",
      path: parsed.pathname,
      query: parsed.search,
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
          if (typeof recordStaffRemediationRequest === "function") {
            recordStaffRemediationRequest({
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
  page = await waitUntil(async () => {
    const candidate = await current(miniProgram);
    const state = await candidate.data();
    return state.challengePrompt ? candidate : null;
  }, 8000, "SMS challenge");
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
  await fillPlaceholder(page, "短信验证码", await inboxCode(phone));
  await tapButton(page, "登录");
  return waitPath(miniProgram, "pages/home/home", 20000);
}

async function openStaffPage(miniProgram) {
  const home = await waitPath(miniProgram, "pages/home/home");
  await tapButton(home, "员工管理与状态");
  await waitPath(miniProgram, "pages/supervisor/staff");
  return waitUntil(async () => {
    const page = await current(miniProgram);
    const data = await page.data();
    return !data.loading && Number(data.propertyId) === objects.primary_property_id
      ? page
      : null;
  }, 30000, "paginated staff page");
}

async function selectEmployee(page, employeeId) {
  let card = await page.$(`.employee-card[data-id="${employeeId}"]`);
  if (!card) {
    const cards = await page.$$(".employee-card");
    for (const candidate of cards) {
      if (Number(await candidate.attribute("data-id")) === Number(employeeId)) {
        card = candidate;
        break;
      }
    }
  }
  if (!card) {
    throw new Error(`employee card missing ${employeeId}`);
  }
  await card.tap();
  return waitUntil(async () => {
    const state = await page.data();
    return Number(state.selectedEmployeeId) === Number(employeeId) ? page : null;
  }, 5000, `employee selection ${employeeId}`);
}

async function pickerByText(page, expected, takeLast) {
  const pickers = await page.$$("picker");
  let matched = null;
  for (const picker of pickers) {
    const text = ((await picker.text()) || "").trim();
    if (text.indexOf(expected) >= 0) {
      if (!takeLast) {
        return picker;
      }
      matched = picker;
    }
  }
  if (!matched) {
    throw new Error(`picker missing ${expected}`);
  }
  return matched;
}

(async () => {
  const miniProgram = await automator.connect({ wsEndpoint: WS });
  try {
    await attachRequestLogger(miniProgram);
    await miniProgram.callWxMethod("clearStorageSync");
    await miniProgram.reLaunch("/pages/login/login");
    await smsLogin(miniProgram, CONTEXT.remediation.manager_phone);
    let page = await openStaffPage(miniProgram);
    let data = await page.data();
    const fixtureCount = (data.items || []).filter((item) =>
      String(item.employee.employee_no || "").startsWith("PAGE-")
    ).length;
    const historicalEnded = (data.items || []).find(
      (item) => Number(item.employee.id) === objects.ended_employee_id
    );
    const pagedRequests = requests.filter(
      (item) =>
        item.path === "/api/v1/admin/employees" &&
        item.query.indexOf("after_id=") >= 0
    );
    record(
      "STAFF-ROSTER-PAGINATION-PAGE",
      fixtureCount === 101 && historicalEnded && pagedRequests.length >= 1 ? "PASS" : "FAIL",
      `visible=${(data.items || []).length}; fixture_visible=${fixtureCount}; historical_employee_visible=${Boolean(
        historicalEnded
      )}; cursor_requests=${pagedRequests.length}`
    );

    const codeSuffix = CONTEXT.run_id.split("-").slice(-1)[0].toUpperCase();
    if (!(data.duties || []).some((item) => item.code === `INSPECTION_${codeSuffix}`)) {
      await fillPlaceholder(page, "职责编码", `INSPECTION_${codeSuffix}`);
      await fillPlaceholder(page, "职责名称", "巡检职责");
      await tapButton(page, "新增职责");
      page = await waitUntil(async () => {
        const candidate = await current(miniProgram);
        const state = await candidate.data();
        return (state.duties || []).some(
          (item) => item.code === `INSPECTION_${codeSuffix}`
        )
          ? candidate
          : null;
      }, 10000, "inspection duty");
    }
    data = await page.data();
    if (!(data.skills || []).some((item) => item.code === `HVAC_${codeSuffix}`)) {
      await fillPlaceholder(page, "技能编码", `HVAC_${codeSuffix}`);
      await fillPlaceholder(page, "技能名称", "暖通技能");
      await tapButton(page, "新增技能");
      page = await waitUntil(async () => {
        const candidate = await current(miniProgram);
        const state = await candidate.data();
        return (state.skills || []).some((item) => item.code === `HVAC_${codeSuffix}`)
          ? candidate
          : null;
      }, 10000, "HVAC skill");
    }

    page = await current(miniProgram);
    await selectEmployee(page, objects.primary_employee_id);
    page = await current(miniProgram);
    data = await page.data();
    const newDuty = data.duties.find((item) => item.code === `INSPECTION_${codeSuffix}`);
    const newSkill = data.skills.find((item) => item.code === `HVAC_${codeSuffix}`);
    const editCapacity = await page.$('[data-field="editCapacity"]');
    await editCapacity.input("3");
    const groups = await page.$$("checkbox-group");
    if (groups.length < 4) {
      throw new Error("employee maintenance checkbox groups missing");
    }
    await groups[groups.length - 2].trigger("change", { value: [String(newDuty.id)] });
    await groups[groups.length - 1].trigger("change", { value: [String(newSkill.id)] });
    const departmentPicker = await pickerByText(page, "部门：", true);
    await departmentPicker.trigger("change", { value: 0 });
    await tapButton(page, "保存资料");
    page = await waitUntil(async () => {
      const candidate = await current(miniProgram);
      const state = await candidate.data();
      const selected = state.selected;
      return selected &&
        Number(selected.employee.id) === objects.primary_employee_id &&
        Number(selected.employee.max_capacity) === 3 &&
        selected.employee.department_id == null &&
        (selected.duties || []).some((item) => Number(item.id) === Number(newDuty.id)) &&
        (selected.skills || []).some((item) => Number(item.id) === Number(newSkill.id))
        ? candidate
        : null;
    }, 15000, "employee maintenance clear department");
    data = await page.data();
    const restoreDepartmentIndex = data.departmentOptions.findIndex(
      (item) => Number(item.id) === Number(CONTEXT.org.department_id)
    );
    const restoreDepartmentPicker = await pickerByText(page, "部门：", true);
    await restoreDepartmentPicker.trigger("change", { value: restoreDepartmentIndex });
    await tapButton(page, "保存资料");
    page = await waitUntil(async () => {
      const candidate = await current(miniProgram);
      const state = await candidate.data();
      return state.selected &&
        Number(state.selected.employee.department_id) === Number(CONTEXT.org.department_id)
        ? candidate
        : null;
    }, 15000, "employee department restore");
    const maintainedShot = await screenshot(miniProgram, "fix1-primary-maintained.png");
    record(
      "STAFF-EXISTING-MAINTENANCE-PAGE",
      "PASS",
      `employee_id=${objects.primary_employee_id}; capacity=3; department restored; added duty and skill through PATCH`,
      maintainedShot ? [maintainedShot] : []
    );

    data = await page.data();
    let assignmentIndex = data.assignmentProperties.findIndex(
      (item) => Number(item.id) === objects.secondary_property_id
    );
    if (assignmentIndex < 0) {
      throw new Error("secondary property missing from assignment picker");
    }
    let assignmentPicker = await pickerByText(
      page,
      data.assignmentProperties[data.assignmentPropertyIndex].name,
      true
    );
    await assignmentPicker.trigger("change", { value: assignmentIndex });
    await tapButton(page, "建立任职");
    page = await waitUntil(async () => {
      const candidate = await current(miniProgram);
      const state = await candidate.data();
      return state.selected &&
        (state.selected.assignments || []).some(
          (item) => Number(item.property_id) === objects.secondary_property_id && item.status === "ACTIVE"
        )
        ? candidate
        : null;
    }, 15000, "secondary property assignment");
    record(
      "STAFF-ADD-SERVICE-PROPERTY-PAGE",
      "PASS",
      `employee_id=${objects.primary_employee_id}; property_id=${objects.secondary_property_id}; ACTIVE assignment created through page`
    );

    await selectEmployee(page, objects.ended_employee_id);
    page = await current(miniProgram);
    data = await page.data();
    const endedBefore = !data.selected.hasCurrentAssignment;
    assignmentIndex = data.assignmentProperties.findIndex(
      (item) => Number(item.id) === objects.primary_property_id
    );
    if (assignmentIndex < 0) {
      throw new Error("ended employee cannot select original property");
    }
    assignmentPicker = await pickerByText(
      page,
      data.assignmentProperties[data.assignmentPropertyIndex].name,
      true
    );
    await assignmentPicker.trigger("change", { value: assignmentIndex });
    await tapButton(page, "建立任职");
    page = await waitUntil(async () => {
      const candidate = await current(miniProgram);
      const state = await candidate.data();
      return state.selected &&
        state.selected.hasCurrentAssignment &&
        (state.selected.assignments || []).some(
          (item) => Number(item.property_id) === objects.primary_property_id && item.status === "ACTIVE"
        )
        ? candidate
        : null;
    }, 15000, "historical employee recovery");
    const recoveryShot = await screenshot(miniProgram, "fix1-ended-employee-recovered.png");
    record(
      "STAFF-ENDED-RECOVERY-PAGE",
      endedBefore ? "PASS" : "FAIL",
      `employee_id=${objects.ended_employee_id}; historical_before=${endedBefore}; original property ACTIVE assignment recreated through page`,
      recoveryShot ? [recoveryShot] : []
    );

    save();
    console.log(
      JSON.stringify({
        result_count: results.length,
        all_pass: results.every((item) => item.status === "PASS"),
      })
    );
    await miniProgram.disconnect();
    process.exit(results.every((item) => item.status === "PASS") ? 0 : 2);
  } catch (error) {
    fs.writeFileSync(
      path.join(OUT, "remediation-page-error.txt"),
      `${error && error.message}\n${error && error.stack}\n`,
      "utf8"
    );
    save();
    try {
      await miniProgram.disconnect();
    } catch (disconnectError) {
      // Preserve the original failure.
    }
    console.error(error && error.message);
    process.exit(1);
  }
})();
