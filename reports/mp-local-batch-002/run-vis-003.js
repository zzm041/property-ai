"use strict";

const fs = require("fs");
const path = require("path");
const automator = require(process.env.MP_AUTOMATOR_MODULE);
const { createToolkit } = require("./automator-lib");

const OUT = path.resolve(__dirname);
const {
  writeJson,
  screenshot,
  wait,
  current,
  tapButton,
  tapViewText,
  loginAs,
  waitPath,
} = createToolkit(OUT);

function targetHouseId() {
  try {
    return Number(JSON.parse(fs.readFileSync(path.join(OUT, "002-target-house.json"), "utf8")).house_id);
  } catch (error) {
    return 0;
  }
}

async function tapCardWithId(page, selector, id) {
  const nodes = await page.$$(selector);
  const needle = String(id);
  const labels = [];
  for (const node of nodes) {
    const text = ((await node.text()) || "").replace(/\s+/g, " ").trim();
    labels.push(text);
    if (text === needle || text.indexOf(`${needle} `) === 0 || text.indexOf(`工单 ${needle}`) === 0) {
      await node.tap();
      return labels;
    }
  }
  throw new Error(`card ${needle} not found; have ${labels.slice(0, 20).join("|")}`);
}

async function loadQueueUntil(miniProgram, workOrderId) {
  let page = await current(miniProgram);
  for (let i = 0; i < 20; i += 1) {
    const data = await page.data();
    const found = (data.items || []).some((row) => String(row.id) === String(workOrderId));
    if (found) {
      return data;
    }
    if (!data.nextCursor) {
      writeJson("003-queue-missing.json", {
        looking_for: workOrderId,
        count: (data.items || []).length,
        ids: (data.items || []).map((row) => row.id),
      });
      throw new Error(`work order ${workOrderId} not in supervisor queue after paging`);
    }
    await tapButton(page, "加载更多");
    await wait(1200);
    page = await current(miniProgram);
  }
  throw new Error("queue paging exceeded");
}

(async () => {
  const miniProgram = await automator.connect({ wsEndpoint: "ws://127.0.0.1:9420" });
  const houseId = targetHouseId();
  const result = { task_id: "MP-LOCAL-VIS-003", house_id: houseId };

  await loginAs(miniProgram, "以员工登录");
  let page = await current(miniProgram);
  await tapButton(page, "今日考勤");
  page = await waitPath(miniProgram, "pages/attendance/attendance");
  await wait(600);
  await tapButton(page, "签到");
  await wait(1200);
  page = await current(miniProgram);
  const attendance = await page.data();
  writeJson("003-staff-checkin.json", {
    propertyId: attendance.propertyId,
    snapshot: attendance.snapshot,
    error: attendance.error,
  });
  await screenshot(miniProgram, "003-staff-checkin.png");
  if (attendance.error && !attendance.snapshot) {
    throw new Error(`check-in failed: ${attendance.error}`);
  }

  await loginAs(miniProgram, "以住户登录");
  page = await current(miniProgram);
  await tapButton(page, "报修");
  page = await waitPath(miniProgram, "pages/work-orders/create");
  await wait(600);
  await page.setData({
    houseId: String(houseId),
    text: "MP-LOCAL-VIS-003 kitchen leak in room 102",
  });
  await screenshot(miniProgram, "003-resident-create.png");
  await tapButton(page, "提交报修");
  page = await waitPath(miniProgram, "pages/work-orders/detail");
  await wait(1200);
  page = await current(miniProgram);
  const created = await page.data();
  const workOrder = created.workOrder || {};
  result.work_order_id = workOrder.id;
  result.status_after_create = workOrder.status;
  writeJson("003-resident-created.json", {
    id: workOrder.id,
    status: workOrder.status,
    error: created.error,
  });
  await screenshot(miniProgram, "003-resident-created.png");
  if (!workOrder.id) {
    throw new Error("create did not open a work order");
  }

  await loginAs(miniProgram, "以物业登录");
  page = await current(miniProgram);
  await tapButton(page, "待派工单");
  page = await waitPath(miniProgram, "pages/supervisor/queue");
  await wait(1000);
  const queue = await loadQueueUntil(miniProgram, workOrder.id);
  page = await current(miniProgram);
  writeJson("003-queue-before-assign.json", {
    selectedId: queue.selectedId,
    employeeId: queue.employeeId,
    staff: queue.staff,
    nextCursor: queue.nextCursor,
    visible_ids: (queue.items || []).map((row) => row.id),
    error: queue.error,
  });
  await tapCardWithId(page, ".card", workOrder.id);
  const staffRow = (queue.staff || []).find((row) => row.on_duty) || (queue.staff || [])[0];
  if (!staffRow) {
    throw new Error("no staff daily row after check-in");
  }
  result.employee_id = staffRow.employee_id;
  await tapViewText(page, ".empty", `员工 ${staffRow.employee_id}`);
  await screenshot(miniProgram, "003-queue-selected.png");
  await tapButton(page, "派单");
  await wait(2000);
  page = await current(miniProgram);
  const afterAssign = await page.data();
  writeJson("003-queue-after-assign.json", {
    error: afterAssign.error,
    staff: afterAssign.staff,
  });
  await screenshot(miniProgram, "003-queue-after-assign.png");
  if (afterAssign.error) {
    throw new Error(`assign failed: ${afterAssign.error}`);
  }

  await loginAs(miniProgram, "以员工登录");
  page = await current(miniProgram);
  await tapButton(page, "我的任务");
  page = await waitPath(miniProgram, "pages/staff-orders/list");
  await wait(800);
  await tapCardWithId(page, ".card", workOrder.id);
  page = await waitPath(miniProgram, "pages/staff-orders/detail");
  await wait(800);
  page = await current(miniProgram);
  let staffDetail = await page.data();
  writeJson("003-staff-detail-assigned.json", {
    status: staffDetail.workOrder && staffDetail.workOrder.status,
    canAccept: staffDetail.canAccept,
    error: staffDetail.error,
  });
  await screenshot(miniProgram, "003-staff-assigned.png");
  if (staffDetail.canAccept) {
    await tapButton(page, "接单");
    await wait(1200);
    page = await current(miniProgram);
  }
  staffDetail = await page.data();
  if (staffDetail.canStart) {
    await tapButton(page, "开始处理");
    await wait(1200);
    page = await current(miniProgram);
  }
  staffDetail = await page.data();
  writeJson("003-staff-detail-processing.json", {
    status: staffDetail.workOrder && staffDetail.workOrder.status,
    canFinish: staffDetail.canFinish,
    error: staffDetail.error,
  });
  if (staffDetail.canFinish) {
    await tapButton(page, "填写回执并完成");
    await wait(1500);
    page = await current(miniProgram);
  }
  staffDetail = await page.data();
  result.status_after_staff = staffDetail.workOrder && staffDetail.workOrder.status;
  writeJson("003-staff-detail-receipt.json", {
    status: result.status_after_staff,
    logs: (staffDetail.logs || []).map((row) => ({
      id: row.id,
      from_status: row.from_status,
      to_status: row.to_status,
      event_code: row.event_code,
    })),
    error: staffDetail.error,
  });
  await screenshot(miniProgram, "003-staff-receipt.png");

  await loginAs(miniProgram, "以住户登录");
  page = await current(miniProgram);
  await tapButton(page, "我的工单");
  page = await waitPath(miniProgram, "pages/work-orders/list");
  await wait(800);
  await tapCardWithId(page, ".card", workOrder.id);
  page = await waitPath(miniProgram, "pages/work-orders/detail");
  await wait(800);
  page = await current(miniProgram);
  let residentDetail = await page.data();
  writeJson("003-resident-before-confirm.json", {
    status: residentDetail.workOrder && residentDetail.workOrder.status,
    canConfirm: residentDetail.canConfirm,
    error: residentDetail.error,
  });
  await screenshot(miniProgram, "003-resident-wait-confirm.png");
  if (residentDetail.canConfirm) {
    await tapButton(page, "确认解决");
    await wait(1500);
    page = await current(miniProgram);
  }
  residentDetail = await page.data();
  result.status_after_confirm = residentDetail.workOrder && residentDetail.workOrder.status;
  await screenshot(miniProgram, "003-resident-confirmed.png");

  await tapButton(page, "刷新");
  await wait(1000);
  page = await current(miniProgram);
  const reentered = await page.data();
  result.status_reentered = reentered.workOrder && reentered.workOrder.status;
  result.logs = (reentered.logs || []).map((row) => ({
    id: row.id,
    from_status: row.from_status,
    to_status: row.to_status,
    event_code: row.event_code,
  }));
  writeJson("003-resident-reentered.json", {
    status: result.status_reentered,
    logs: result.logs,
    error: reentered.error,
  });
  await screenshot(miniProgram, "003-resident-reentered.png");
  writeJson("003-result.json", result);
  miniProgram.disconnect();
})().catch((error) => {
  fs.writeFileSync(
    path.join(OUT, "003-automator-error.txt"),
    String(error && error.stack ? error.stack : error),
    "utf8"
  );
  process.exit(1);
});
