"use strict";

const fs = require("fs");
const path = require("path");
const automator = require(process.env.MP_AUTOMATOR_MODULE);

const OUT = __dirname;
const REPO = path.resolve(OUT, "..", "..");
const ACCOUNTS = JSON.parse(
  fs.readFileSync(
    path.join(OUT, "..", "mp-current-full-regression", "accounts.local.json"),
    "utf8"
  )
);
const STATE = JSON.parse(fs.readFileSync(path.join(OUT, "runtime-state.json"), "utf8"));
const WS = process.env.MP_AUTOMATOR_WS || "ws://127.0.0.1:9435";

function wait(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function current(miniProgram) {
  return miniProgram.currentPage();
}

async function waitPath(miniProgram, fragment) {
  for (let attempt = 0; attempt < 50; attempt += 1) {
    const page = await current(miniProgram);
    if (page && page.path && page.path.includes(fragment)) {
      return page;
    }
    await wait(300);
  }
  const page = await current(miniProgram);
  throw new Error(`Expected ${fragment}; current=${page && page.path}`);
}

async function login(miniProgram) {
  await miniProgram.callWxMethod("clearStorageSync");
  await miniProgram.callWxMethod("reLaunch", { url: "/pages/login/login" });
  let page = await waitPath(miniProgram, "pages/login/login");
  const data = await page.data();
  if (data.mode !== "password") {
    const toggles = await page.$$(".link-text");
    await toggles[toggles.length - 1].tap();
    await wait(300);
    page = await current(miniProgram);
  }
  const inputs = await page.$$("input");
  if (inputs.length < 2) {
    throw new Error("Password login inputs were not rendered");
  }
  await inputs[0].input(ACCOUNTS.phones.resident);
  await inputs[1].input(ACCOUNTS.password);
  const primary = await page.$$("button.btn");
  if (!primary.length) {
    throw new Error("Password login button was not rendered");
  }
  await primary[0].tap();
  return waitPath(miniProgram, "pages/home/home");
}

function latestApiLog() {
  const directory = path.join(REPO, "tmp", "auth001-runtime");
  return fs
    .readdirSync(directory)
    .filter((name) => /^api-.*\.out\.log$/.test(name))
    .map((name) => ({
      name,
      fullPath: path.join(directory, name),
      mtime: fs.statSync(path.join(directory, name)).mtimeMs,
    }))
    .sort((left, right) => right.mtime - left.mtime)[0];
}

(async () => {
  const miniProgram = await automator.connect({ wsEndpoint: WS });
  await login(miniProgram);
  await miniProgram.callWxMethod("navigateTo", {
    url: `/pages/work-orders/detail?id=${STATE.work_order_id}`,
  });
  let page = await waitPath(miniProgram, "pages/work-orders/detail");
  let data;
  for (let attempt = 0; attempt < 40; attempt += 1) {
    data = await page.data();
    if (
      data.workOrder &&
      Number(data.workOrder.id) === Number(STATE.work_order_id) &&
      Array.isArray(data.media) &&
      data.media.length
    ) {
      break;
    }
    await wait(300);
    page = await current(miniProgram);
  }
  const media = (data.media || []).find(
    (item) => Number(item.file_id) === Number(STATE.file_id)
  );
  if (!media) {
    throw new Error("The linked persistent media was not rendered on the detail page");
  }
  await miniProgram.screenshot({ path: path.join(OUT, "01-detail-with-media.png") });

  const apiLog = latestApiLog();
  const before = fs.readFileSync(apiLog.fullPath, "utf8");
  const buttons = await page.$$("button.btn");
  let mediaButton = null;
  for (const button of buttons) {
    if (Number(await button.attribute("data-file-id")) === Number(STATE.file_id)) {
      mediaButton = button;
      break;
    }
  }
  if (!mediaButton) {
    throw new Error("The image preview button was not rendered for the linked file");
  }
  await mediaButton.tap();
  await wait(1500);
  let previewScreenshot = true;
  try {
    await miniProgram.screenshot({ path: path.join(OUT, "02-after-preview-tap.png") });
  } catch (_error) {
    previewScreenshot = false;
  }
  await wait(500);
  const after = fs.readFileSync(apiLog.fullPath, "utf8");
  const delta = after.slice(before.length);
  const downloadCompleted = delta
    .split(/\r?\n/)
    .filter(Boolean)
    .some((line) => {
      try {
        const event = JSON.parse(line);
        return (
          event.method === "GET" &&
          event.path === `/api/v1/files/${STATE.file_id}/content` &&
          event.event === "request_completed" &&
          event.status_code === 200
        );
      } catch (_error) {
        return false;
      }
    });
  page = await current(miniProgram);
  const finalData = page && page.path.includes("pages/work-orders/detail")
    ? await page.data()
    : data;
  const evidence = {
    task_id: "PERSISTENT-MEDIA-001-PAGE",
    automator_ws: WS,
    api_log: apiLog.name,
    login_interaction: "resident password fields and login button",
    detail_route: `pages/work-orders/detail?id=${STATE.work_order_id}`,
    work_order_id: STATE.work_order_id,
    work_order_status: data.workOrder.status,
    file_id: STATE.file_id,
    media_kind: media.kind,
    media_button_tapped: true,
    download_200_observed_after_tap: downloadCompleted,
    page_error_after_tap: finalData.error || "",
    screenshots: [
      "01-detail-with-media.png",
      ...(previewScreenshot ? ["02-after-preview-tap.png"] : []),
    ],
    not_claimed: ["real VLM", "production storage", "Production V1"],
    redaction: "No credential, phone, token, resident name, or file bytes are stored.",
  };
  fs.writeFileSync(
    path.join(OUT, "page-evidence.json"),
    `${JSON.stringify(evidence, null, 2)}\n`,
    "utf8"
  );
  console.log(JSON.stringify(evidence));
  process.exit(downloadCompleted && !evidence.page_error_after_tap ? 0 : 2);
})().catch((error) => {
  fs.writeFileSync(
    path.join(OUT, "page-evidence-error.txt"),
    `${error.message}\n${error.stack || ""}\n`,
    "utf8"
  );
  console.error(error.message);
  process.exit(1);
});
