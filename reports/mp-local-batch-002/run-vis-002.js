"use strict";

const path = require("path");
const automator = require(process.env.MP_AUTOMATOR_MODULE);
const { createToolkit } = require("./automator-lib");

const OUT = path.resolve(__dirname);
const { writeJson, screenshot, wait, current, tapButton, loginAs, waitPath } =
  createToolkit(OUT);

function redactRequests(rows) {
  return (rows || []).map((row) => ({
    method: row.house_id ? undefined : row.method,
    id: row.id,
    house_id: row.house_id,
    status: row.status,
    relation_type: row.relation_type,
    room_no: row.room_no,
    relation_id: row.relation_id,
  }));
}

(async () => {
  const miniProgram = await automator.connect({ wsEndpoint: "ws://127.0.0.1:9420" });
  const result = {
    task_id: "MP-LOCAL-VIS-002",
    reused_existing_pending: false,
    created_new_request: false,
  };

  await loginAs(miniProgram, "以住户登录");
  let page = await current(miniProgram);
  const residentHome = await page.data();
  writeJson("002-resident-home.json", {
    path: page.path,
    display_name: residentHome.me && residentHome.me.display_name,
    roles: residentHome.me && residentHome.me.roles,
    propertyId: residentHome.propertyId,
    tenantId: residentHome.tenantId,
  });
  await tapButton(page, "我的房屋 / 绑定");
  page = await waitPath(miniProgram, "pages/houses/houses");
  await wait(800);
  const before = await page.data();
  writeJson("002-houses-before.json", {
    path: page.path,
    items: redactRequests(before.items),
    requests: redactRequests(before.requests),
    houseId: before.houseId,
    error: before.error,
  });
  await screenshot(miniProgram, "002-houses-before.png");

  const pending = (before.requests || []).find((row) => row.status === "PENDING");
  const boundIds = (before.items || []).map((row) => Number(row.house_id));
  let authorizedHouseId = 0;
  try {
    authorizedHouseId = Number(
      JSON.parse(require("fs").readFileSync(path.join(OUT, "002-target-house.json"), "utf8")).house_id
    );
  } catch (error) {
    authorizedHouseId = 0;
  }
  let targetHouseId = pending ? Number(pending.house_id) : 0;
  if (!targetHouseId && authorizedHouseId && boundIds.indexOf(authorizedHouseId) < 0) {
    targetHouseId = authorizedHouseId;
  }
  if (!targetHouseId) {
    const typed = Number(before.houseId);
    if (typed && boundIds.indexOf(typed) < 0) {
      targetHouseId = typed;
    }
  }

  if (pending) {
    result.reused_existing_pending = true;
    result.bind_request_id = pending.id;
    result.house_id = pending.house_id;
  } else {
    const applyId = targetHouseId || Number(before.houseId) || 0;
    if (applyId) {
      await page.setData({ houseId: String(applyId) });
      await tapButton(page, "提交绑定申请");
      await wait(1500);
      page = await current(miniProgram);
      const afterApply = await page.data();
      writeJson("002-houses-after-apply.json", {
        items: redactRequests(afterApply.items),
        requests: redactRequests(afterApply.requests),
        error: afterApply.error,
        applied_house_id: applyId,
      });
      await screenshot(miniProgram, "002-houses-after-apply.png");
      const created = (afterApply.requests || []).find(
        (row) => Number(row.house_id) === applyId && row.status === "PENDING"
      );
      if (created) {
        result.created_new_request = true;
        result.bind_request_id = created.id;
        result.house_id = created.house_id;
      } else {
        result.apply_error = afterApply.error || "apply did not create PENDING request";
        result.applied_house_id = applyId;
        result.existing_active_house_ids = boundIds;
      }
    } else {
      result.apply_error =
        "no house id on page; existing ACTIVE relations were not recreated";
    }
  }

  await loginAs(miniProgram, "以物业登录");
  page = await current(miniProgram);
  const managerHome = await page.data();
  writeJson("002-manager-home.json", {
    display_name: managerHome.me && managerHome.me.display_name,
    propertyId: managerHome.propertyId,
    tenantId: managerHome.tenantId,
  });
  await tapButton(page, "绑定审核");
  page = await waitPath(miniProgram, "pages/supervisor/bindings");
  await wait(800);
  let bindings = await page.data();
  writeJson("002-bindings-before.json", {
    propertyId: bindings.propertyId,
    tenantId: bindings.tenantId,
    items: redactRequests(bindings.items),
    error: bindings.error,
  });
  await screenshot(miniProgram, "002-bindings-before.png");

  if (result.bind_request_id) {
    const target = String(result.bind_request_id);
    const cards = await page.$$("button");
    let approved = false;
    for (const button of cards) {
      const text = ((await button.text()) || "").trim();
      if (text !== "通过") {
        continue;
      }
      const wxml = await button.wxml();
      if (wxml && wxml.indexOf(target) >= 0) {
        await button.tap();
        approved = true;
        break;
      }
    }
    if (!approved) {
      const firstApprove = (await page.$$("button")).find
        ? null
        : null;
      const buttons = await page.$$("button");
      for (const button of buttons) {
        if (((await button.text()) || "").trim() === "通过") {
          await button.tap();
          approved = true;
          break;
        }
      }
    }
    result.approved_on_page = approved;
    await wait(1500);
    page = await current(miniProgram);
    bindings = await page.data();
    writeJson("002-bindings-after.json", {
      items: redactRequests(bindings.items),
      error: bindings.error,
    });
    await screenshot(miniProgram, "002-bindings-after.png");
  }

  await loginAs(miniProgram, "以住户登录");
  page = await current(miniProgram);
  await tapButton(page, "我的房屋 / 绑定");
  page = await waitPath(miniProgram, "pages/houses/houses");
  await wait(800);
  const after = await page.data();
  writeJson("002-houses-after.json", {
    items: redactRequests(after.items),
    requests: redactRequests(after.requests),
    error: after.error,
  });
  await screenshot(miniProgram, "002-houses-after.png");
  result.after_items = redactRequests(after.items);
  result.after_requests = redactRequests(after.requests);
  writeJson("002-result.json", result);
  miniProgram.disconnect();
})().catch((error) => {
  require("fs").writeFileSync(
    path.join(OUT, "002-automator-error.txt"),
    String(error && error.stack ? error.stack : error),
    "utf8"
  );
  process.exit(1);
});
