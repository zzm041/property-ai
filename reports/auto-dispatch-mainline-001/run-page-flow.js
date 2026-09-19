"use strict";

const fs = require("fs");
const http = require("http");
const path = require("path");
const automator = require(process.env.MP_AUTOMATOR_MODULE);

const OUT = __dirname;
const ACCOUNTS = JSON.parse(
  fs.readFileSync(path.join(OUT, "..", "mp-current-full-regression", "accounts.local.json"), "utf8")
);
const PUBLIC = JSON.parse(
  fs.readFileSync(path.join(OUT, "..", "mp-current-full-regression", "accounts-public.json"), "utf8")
);
const WS = process.env.MP_AUTOMATOR_WS || "ws://127.0.0.1:9435";

function wait(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function postJson(pathname, body) {
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
          "X-Request-Id": "auto-dispatch-page-flow",
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
            payload = raw ? JSON.parse(raw) : {};
          } catch (error) {
            reject(new Error("Development SMS inbox returned invalid JSON"));
            return;
          }
          if (response.statusCode >= 400) {
            reject(new Error(`Development SMS inbox status ${response.statusCode}`));
            return;
          }
          resolve(payload);
        });
      }
    );
    request.on("error", reject);
    request.write(data);
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

async function waitPath(miniProgram, fragment) {
  for (let attempt = 0; attempt < 40; attempt += 1) {
    const page = await current(miniProgram);
    if (page && page.path && page.path.includes(fragment)) {
      return page;
    }
    await wait(300);
  }
  const page = await current(miniProgram);
  throw new Error(`Expected ${fragment}; current=${page && page.path}`);
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
  await wait(500);
  page = await current(miniProgram);
  const prompt = (await page.data()).challengePrompt;
  await fill(page, "挑战答案", challengeAnswer(prompt));
  await tap(page, "获取验证码");
  await wait(500);
  const inbox = await postJson("/auth/sms/test-inbox", { phone, purpose: "login" });
  await fill(await current(miniProgram), "短信验证码", inbox.data.code);
  await tap(await current(miniProgram), "登录");
  return waitPath(miniProgram, "pages/home/home");
}

(async () => {
  const miniProgram = await automator.connect({ wsEndpoint: WS });
  const resumeWorkOrderId = Number(process.env.RESUME_WORK_ORDER_ID || 0);
  let attendanceData = {
    snapshot: { business_date: process.env.RESUME_BUSINESS_DATE || null },
  };
  let page;
  if (!resumeWorkOrderId) {
    await resetToLogin(miniProgram);
    await smsLogin(miniProgram, ACCOUNTS.phones.staff);
    await miniProgram.callWxMethod("navigateTo", {
      url: `/pages/attendance/attendance?propertyId=${PUBLIC.org.property_id}`,
    });
    page = await waitPath(miniProgram, "pages/attendance/attendance");
    await fill(page, "小区 ID", PUBLIC.org.property_id);
    await tap(page, "签到");
    await wait(700);
    attendanceData = await page.data();
    if (!attendanceData.snapshot || attendanceData.error) {
      throw new Error(`Staff page check-in failed: ${attendanceData.error || "no snapshot"}`);
    }
    await miniProgram.screenshot({
      path: path.join(OUT, "staff-current-day-checkin.png"),
    });

    await resetToLogin(miniProgram);
    await smsLogin(miniProgram, ACCOUNTS.phones.resident);
    await miniProgram.callWxMethod("navigateTo", {
      url: `/pages/work-orders/create?houseId=${PUBLIC.org.house_id}`,
    });
    page = await waitPath(miniProgram, "pages/work-orders/create");
    await fill(page, "房屋 ID", PUBLIC.org.house_id);
    await fill(page, "报修说明", "本轮页面自动派单测试：厨房插座出现火花，请检修。");
    await tap(page, "提交报修");
    page = await waitPath(miniProgram, "pages/work-orders/detail");
  } else {
    page = await current(miniProgram);
    const currentData = page && page.path.includes("pages/work-orders/detail")
      ? await page.data()
      : null;
    if (!currentData || !currentData.workOrder || currentData.workOrder.id !== resumeWorkOrderId) {
      await miniProgram.callWxMethod("reLaunch", {
        url: `/pages/work-orders/detail?id=${resumeWorkOrderId}`,
      });
      page = await waitPath(miniProgram, "pages/work-orders/detail");
    }
  }

  let data = null;
  for (let attempt = 0; attempt < 30; attempt += 1) {
    data = await page.data();
    if (data.workOrder && data.workOrder.status === "ASSIGNED" && data.autoDispatched) {
      break;
    }
    await wait(500);
    page = await current(miniProgram);
  }
  if (!data || !data.workOrder) {
    throw new Error("Resident detail did not load the created work order");
  }
  const workOrderId = resumeWorkOrderId || data.workOrder.id;
  await miniProgram.screenshot({
    path: path.join(OUT, "resident-auto-assigned.png"),
  });

  await resetToLogin(miniProgram);
  await smsLogin(miniProgram, ACCOUNTS.phones.staff);
  await miniProgram.callWxMethod("navigateTo", { url: "/pages/staff-orders/list" });
  page = await waitPath(miniProgram, "pages/staff-orders/list");
  await wait(700);
  const staffData = await page.data();
  const visibleToAssignedStaff = (staffData.items || []).some(
    (item) => String(item.id) === String(workOrderId)
  );
  await miniProgram.screenshot({
    path: path.join(OUT, "staff-auto-assigned-list.png"),
  });

  const result = {
    task_id: "AUTO-DISPATCH-MAINLINE-001-PAGE",
    captured_at: new Date().toISOString(),
    automator_ws: WS,
    staff_checkin_interaction: "staff attendance page and check-in button",
    staff_checkin_business_date: attendanceData.snapshot.business_date,
    create_interaction: "resident page form and submit button",
    work_order_id: workOrderId,
    resident_detail_status: data.workOrder.status,
    resident_detail_auto_dispatched: Boolean(data.autoDispatched),
    resident_detail_log_events: (data.logs || []).map((item) => item.event_code),
    visible_to_assigned_staff: visibleToAssignedStaff,
    staff_list_status: (staffData.items || [])
      .filter((item) => String(item.id) === String(workOrderId))
      .map((item) => item.status),
    manual_supervisor_interaction_used: false,
    screenshots: [
      "staff-current-day-checkin.png",
      "resident-auto-assigned.png",
      "staff-auto-assigned-list.png",
    ],
    not_claimed: ["real AI", "real SMS", "Production V1", "formal release"],
    redaction: "No phone, SMS code, token, credential, or resident name is stored.",
  };
  fs.writeFileSync(
    path.join(OUT, "page-evidence.json"),
    `${JSON.stringify(result, null, 2)}\n`,
    "utf8"
  );
  console.log(JSON.stringify(result));
  process.exit(
    data.workOrder.status === "ASSIGNED" && data.autoDispatched && visibleToAssignedStaff ? 0 : 2
  );
})().catch((error) => {
  fs.writeFileSync(
    path.join(OUT, "page-flow-error.txt"),
    `${error.message}\n${error.stack || ""}\n`,
    "utf8"
  );
  console.error(error.message);
  process.exit(1);
});
