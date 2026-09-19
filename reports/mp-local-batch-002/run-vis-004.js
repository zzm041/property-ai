"use strict";

const fs = require("fs");
const path = require("path");
const automator = require(process.env.MP_AUTOMATOR_MODULE);
const { createToolkit } = require("./automator-lib");

const OUT = path.resolve(__dirname);
const { writeJson, screenshot, wait, current, tapButton, tapViewText, loginAs, waitPath } =
  createToolkit(OUT);

(async () => {
  const miniProgram = await automator.connect({ wsEndpoint: "ws://127.0.0.1:9420" });
  const result = { task_id: "MP-LOCAL-VIS-004" };

  await loginAs(miniProgram, "以住户登录");
  let page = await current(miniProgram);
  await tapButton(page, "公告");
  page = await waitPath(miniProgram, "pages/announcements/list");
  await wait(800);
  const list = await page.data();
  writeJson("004-announcements-list.json", {
    propertyId: list.propertyId,
    items: (list.items || []).map((row) => ({
      id: row.id,
      title: row.title,
      status: row.status,
    })),
    error: list.error,
  });
  await screenshot(miniProgram, "004-announcements-list.png");
  const published = (list.items || []).find((row) => row.status === "PUBLISHED") || (list.items || [])[0];
  if (!published) {
    throw new Error("no announcement on list");
  }
  result.announcement_id = published.id;
  await tapViewText(page, ".card", published.title || String(published.id));
  page = await waitPath(miniProgram, "pages/announcements/detail");
  await wait(800);
  const detail = await page.data();
  writeJson("004-announcement-detail.json", {
    title: detail.title,
    status: detail.status,
    has_content: Boolean(detail.content),
    error: detail.error,
  });
  await screenshot(miniProgram, "004-announcement-detail.png");

  await miniProgram.navigateBack();
  await wait(400);
  await miniProgram.reLaunch("/pages/home/home");
  await wait(800);
  page = await current(miniProgram);
  await tapButton(page, "通知");
  page = await waitPath(miniProgram, "pages/notifications/list");
  await wait(800);
  let notes = await page.data();
  writeJson("004-notifications-before.json", {
    unread: notes.unread,
    items: (notes.items || []).map((row) => ({
      id: row.id,
      title: row.title,
      read_at: row.read_at || null,
    })),
    error: notes.error,
  });
  await screenshot(miniProgram, "004-notifications-before.png");
  const unread = (notes.items || []).find((row) => !row.read_at) || (notes.items || [])[0];
  if (unread) {
    result.notification_id = unread.id;
    await tapViewText(page, ".card", unread.title || String(unread.id));
    await wait(1200);
    page = await current(miniProgram);
    notes = await page.data();
  }
  writeJson("004-notifications-after.json", {
    unread: notes.unread,
    items: (notes.items || []).map((row) => ({
      id: row.id,
      title: row.title,
      read_at: row.read_at || null,
    })),
    error: notes.error,
  });
  await screenshot(miniProgram, "004-notifications-after.png");

  await miniProgram.reLaunch("/pages/home/home");
  await wait(800);
  page = await current(miniProgram);
  await tapButton(page, "知识助手");
  page = await waitPath(miniProgram, "pages/assistant/assistant");
  await wait(600);
  await page.setData({ question: "装修时间是什么？" });
  await tapButton(page, "提问");
  await wait(2500);
  page = await current(miniProgram);
  const assistant = await page.data();
  writeJson("004-assistant.json", {
    propertyId: assistant.propertyId,
    question: assistant.question,
    answer: assistant.answer,
    citations: (assistant.citations || []).map((row) => ({
      document_id: row.document_id,
      title: row.title,
      source_locator: row.source_locator,
    })),
    error: assistant.error,
  });
  await screenshot(miniProgram, "004-assistant.png");
  result.assistant_has_answer = Boolean(assistant.answer);
  result.citation_count = (assistant.citations || []).length;
  writeJson("004-result.json", result);
  miniProgram.disconnect();
})().catch((error) => {
  fs.writeFileSync(
    path.join(OUT, "004-automator-error.txt"),
    String(error && error.stack ? error.stack : error),
    "utf8"
  );
  process.exit(1);
});
