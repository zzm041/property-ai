"use strict";

const fs = require("fs");
const http = require("http");
const path = require("path");
const automator = require(process.env.MP_AUTOMATOR_MODULE);

const OUT = path.resolve(__dirname);
const WS = process.env.MP_AUTOMATOR_WS || "ws://127.0.0.1:9435";
const CODE_SHA = process.env.CODE_SHA;
const ACCOUNTS = JSON.parse(
  fs.readFileSync(path.resolve(__dirname, "../mp-current-full-regression/accounts.local.json"), "utf8")
);
const RUN_ID = `ann007-page-${Date.now().toString(36)}`;

function wait(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function waitUntil(check, label, timeoutMs) {
  const started = Date.now();
  while (Date.now() - started < (timeoutMs || 20000)) {
    const value = await check();
    if (value) return value;
    await wait(250);
  }
  throw new Error(`timeout waiting for ${label}`);
}

function requestJson(method, pathname, body) {
  return new Promise((resolve, reject) => {
    const data = JSON.stringify(body || {});
    const request = http.request(
      {
        host: "127.0.0.1",
        port: 8083,
        path: `/api/v1${pathname}`,
        method,
        headers: {
          "Content-Type": "application/json",
          "Content-Length": Buffer.byteLength(data),
          "X-Request-Id": `${RUN_ID}-inbox`,
        },
      },
      (response) => {
        let raw = "";
        response.on("data", (chunk) => {
          raw += chunk;
        });
        response.on("end", () => {
          let json = {};
          try {
            json = raw ? JSON.parse(raw) : {};
          } catch (error) {
            json = {};
          }
          resolve({ status: response.statusCode, json });
        });
      }
    );
    request.on("error", reject);
    request.write(data);
    request.end();
  });
}

function answer(prompt) {
  const match = String(prompt || "").match(/^(\d+)\s*\+\s*(\d+)\s*=\s*\?$/);
  if (!match) throw new Error("unsupported challenge prompt");
  return String(Number(match[1]) + Number(match[2]));
}

async function current(miniProgram) {
  return miniProgram.currentPage();
}

async function waitPath(miniProgram, fragment) {
  return waitUntil(async () => {
    const page = await current(miniProgram);
    return page && page.path && page.path.indexOf(fragment) >= 0 ? page : null;
  }, fragment);
}

async function tapButton(page, label) {
  const buttons = await page.$$("button");
  const seen = [];
  for (const button of buttons) {
    const text = ((await button.text()) || "").trim();
    seen.push(text);
    if (text === label) {
      await button.tap();
      return;
    }
  }
  throw new Error(`button not found: ${label}; have ${seen.join("|")}`);
}

async function tapContains(page, selector, expected) {
  const nodes = await page.$$(selector);
  for (const node of nodes) {
    const text = ((await node.text()) || "").replace(/\s+/g, " ").trim();
    if (text.indexOf(expected) >= 0) {
      await node.tap();
      return;
    }
  }
  throw new Error(`text not found: ${expected}`);
}

async function fill(page, placeholder, value) {
  const nodes = (await page.$$("input")).concat(await page.$$("textarea"));
  for (const node of nodes) {
    if ((await node.attribute("placeholder")) === placeholder) {
      await node.tap();
      await node.input(String(value));
      return;
    }
  }
  throw new Error(`field not found: ${placeholder}`);
}

async function developmentCode(phone) {
  return waitUntil(async () => {
    const response = await requestJson("POST", "/auth/sms/test-inbox", {
      phone,
      purpose: "login",
    });
    return response.json && response.json.data && response.json.data.code;
  }, "development SMS inbox", 10000);
}

async function smsLogin(miniProgram, phone) {
  await miniProgram.evaluate(() => {
    wx.removeStorageSync("access_token");
    wx.removeStorageSync("refresh_token");
  });
  await miniProgram.reLaunch("/pages/login/login");
  let page = await waitPath(miniProgram, "pages/login/login");
  if ((await page.data()).mode !== "sms") {
    await tapContains(page, ".link-text", "验证码登录");
    await wait(300);
    page = await current(miniProgram);
  }
  await fill(page, "手机号", phone);
  await tapButton(page, "获取验证码");
  await waitUntil(async () => {
    const candidate = await current(miniProgram);
    return (await candidate.data()).challengePrompt ? candidate : null;
  }, "challenge prompt");
  page = await current(miniProgram);
  await fill(page, "挑战答案", answer((await page.data()).challengePrompt));
  await tapButton(page, "获取验证码");
  await waitUntil(async () => {
    const candidate = await current(miniProgram);
    const state = await candidate.data();
    return !state.challengePrompt && Number(state.cooldown) > 0 ? candidate : null;
  }, "SMS delivery");
  const code = await developmentCode(phone);
  page = await current(miniProgram);
  await fill(page, "短信验证码", code);
  await tapButton(page, "登录");
  return waitPath(miniProgram, "pages/home/home");
}

async function openAnnouncements(miniProgram) {
  const home = await waitPath(miniProgram, "pages/home/home");
  await tapButton(home, "公告审核");
  return waitUntil(async () => {
    const page = await current(miniProgram);
    if (!page || page.path !== "pages/supervisor/announcements") return null;
    const state = await page.data();
    return !state.loading && state.propertyId && state.buildings.length ? page : null;
  }, "announcement management page");
}

async function screenshot(miniProgram, scrollTop, name) {
  await miniProgram.callWxMethod("pageScrollTo", { scrollTop, duration: 0 });
  await wait(400);
  await miniProgram.screenshot({ path: path.join(OUT, name) });
}

(async () => {
  if (!CODE_SHA) throw new Error("CODE_SHA is required");
  const miniProgram = await automator.connect({ wsEndpoint: WS });
  try {
    await smsLogin(miniProgram, ACCOUNTS.phones.property_admin);
    let page = await openAnnouncements(miniProgram);
    let state = await page.data();
    if (!state.canPublish) throw new Error("PROPERTY_ADMIN lacks announcement:publish");

    const title = `页面公告 ${RUN_ID}`;
    await fill(page, "标题", title);
    await fill(page, "内容", "页面初稿：用于验证定向、驳回和历史。 ");
    let pickers = await page.$$("picker");
    await pickers[0].trigger("change", { value: 2 });
    await wait(300);
    page = await current(miniProgram);
    pickers = await page.$$("picker");
    await pickers[1].trigger("change", { value: 1 });
    await wait(300);
    await screenshot(miniProgram, 0, "01-targeted-draft-form.png");
    page = await current(miniProgram);
    await tapButton(page, "保存新草稿");
    await waitUntil(async () => {
      const candidate = await current(miniProgram);
      const data = await candidate.data();
      return !data.loading && data.items.some((item) => item.title === title) ? candidate : null;
    }, "created announcement row");

    page = await current(miniProgram);
    await tapContains(page, ".card", title);
    await waitUntil(async () => {
      const candidate = await current(miniProgram);
      const data = await candidate.data();
      return data.selected && data.selected.title === title ? candidate : null;
    }, "selected announcement");
    page = await current(miniProgram);
    const created = await page.data();
    const announcementId = created.selected.id;
    await tapButton(page, "提交审核");
    await waitUntil(async () => {
      const candidate = await current(miniProgram);
      const data = await candidate.data();
      return data.selected && data.selected.status === "PENDING_REVIEW" ? candidate : null;
    }, "pending review");
    page = await current(miniProgram);
    await fill(page, "驳回时必须填写原因", "页面验证：请补充定向说明");
    await tapButton(page, "驳回");
    await waitUntil(async () => {
      const candidate = await current(miniProgram);
      const data = await candidate.data();
      return data.selected && data.selected.status === "REJECTED" ? candidate : null;
    }, "rejected announcement");
    await screenshot(miniProgram, 850, "02-rejected-with-history.png");

    page = await current(miniProgram);
    await fill(page, "标题", `${title} 修订`);
    await fill(page, "内容", "页面修订稿：已补充定向说明。 ");
    await tapButton(page, "保存所选公告修改");
    await waitUntil(async () => {
      const candidate = await current(miniProgram);
      const data = await candidate.data();
      return data.selected && data.selected.status === "DRAFT" && data.selected.title.endsWith("修订")
        ? candidate
        : null;
    }, "revised draft");
    page = await current(miniProgram);
    await tapButton(page, "提交审核");
    await waitUntil(async () => {
      const candidate = await current(miniProgram);
      const data = await candidate.data();
      return data.selected && data.selected.status === "PENDING_REVIEW" ? candidate : null;
    }, "resubmitted review");
    page = await current(miniProgram);
    await tapButton(page, "人工审核通过并发布");
    await waitUntil(async () => {
      const candidate = await current(miniProgram);
      const data = await candidate.data();
      return data.selected && data.selected.status === "PUBLISHED" ? candidate : null;
    }, "published announcement");
    await screenshot(miniProgram, 850, "03-published-edit-review-history.png");

    page = await current(miniProgram);
    state = await page.data();
    const selected = state.selected;
    const evidence = {
      task_id: "ANNOUNCEMENT-GOVERNANCE-007",
      run_id: RUN_ID,
      code_sha: CODE_SHA,
      automator_ws: WS,
      api: "http://127.0.0.1:8083/api/v1",
      auth: "official SMS page controls with DEVELOPMENT SMS provider",
      page: page.path,
      announcement_id: announcementId,
      final_status: selected.status,
      final_title: selected.title,
      scope: selected.scope,
      review_decisions: (selected.reviews || []).map((item) => item.decision),
      review_reasons_present: (selected.reviews || []).map((item) => Boolean(item.reason)),
      history_actions: (selected.history || []).map((item) => item.action),
      actual_controls: [
        "保存新草稿",
        "提交审核",
        "驳回",
        "保存所选公告修改",
        "人工审核通过并发布",
      ],
      picker_boundary: "scope/audience selected through supported automator change events",
      screenshots: [
        "01-targeted-draft-form.png",
        "02-rejected-with-history.png",
        "03-published-edit-review-history.png",
      ],
    };
    fs.writeFileSync(
      path.join(OUT, "page-evidence.json"),
      `${JSON.stringify(evidence, null, 2)}\n`,
      "utf8"
    );
    process.stdout.write(`${JSON.stringify(evidence, null, 2)}\n`);
  } finally {
    await miniProgram.disconnect();
  }
})().catch((error) => {
  process.stderr.write(`${error.stack || error.message}\n`);
  process.exitCode = 1;
});
