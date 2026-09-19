"use strict";

const fs = require("fs");
const path = require("path");
const automator = require(process.env.MP_AUTOMATOR_MODULE);
const { createToolkit } = require("../mp-local-batch-002/automator-lib");

const OUT = path.resolve(__dirname);
const { writeJson, screenshot, wait, current, tapButton, tapViewText, waitPath } = createToolkit(OUT);

function forbiddenLabels(labels) {
  return labels.filter((item) =>
    ["以住户登录", "以物业登录", "以员工登录"].includes(item)
  );
}

(async () => {
  const miniProgram = await automator.connect({ wsEndpoint: "ws://127.0.0.1:9420" });
  try {
    await miniProgram.callWxMethod("clearStorageSync");
  } catch (error) {
    await miniProgram.evaluate(() => wx.clearStorageSync());
  }
  await miniProgram.reLaunch("/pages/login/login");
  await wait(800);
  let page = await waitPath(miniProgram, "pages/login/login");
  const passwordData = await page.data();
  const passwordButtons = [];
  for (const button of await page.$$("button")) {
    passwordButtons.push(((await button.text()) || "").trim());
  }
  await screenshot(miniProgram, "login-password.png");

  await tapViewText(page, ".link-text", "验证码登录");
  await wait(500);
  page = await current(miniProgram);
  const smsData = await page.data();
  const smsButtons = [];
  for (const button of await page.$$("button")) {
    smsButtons.push(((await button.text()) || "").trim());
  }
  await screenshot(miniProgram, "login-sms.png");

  await tapViewText(page, ".link-text", "忘记密码");
  await wait(800);
  page = await waitPath(miniProgram, "pages/password-reset/password-reset");
  const resetButtons = [];
  for (const button of await page.$$("button")) {
    resetButtons.push(((await button.text()) || "").trim());
  }
  await screenshot(miniProgram, "password-reset.png");

  writeJson("login-vis.json", {
    task_id: "AUTH-IDENTITY-AI-001-B",
    path_password: "pages/login/login",
    password_mode: passwordData.mode,
    password_buttons: passwordButtons,
    sms_mode: smsData.mode,
    sms_buttons: smsButtons,
    reset_buttons: resetButtons,
    forbidden_on_password: forbiddenLabels(passwordButtons),
    forbidden_on_sms: forbiddenLabels(smsButtons),
    evidence: ["login-password.png", "login-sms.png", "password-reset.png"],
    not_claimed: ["real SMS", "Production V1", "Day 4 PASS"],
  });
  if (passwordData.mode !== "password") {
    throw new Error(`default mode ${passwordData.mode}`);
  }
  if (smsData.mode !== "sms") {
    throw new Error(`sms mode ${smsData.mode}`);
  }
  if (forbiddenLabels(passwordButtons).length || forbiddenLabels(smsButtons).length) {
    throw new Error("seed identity buttons still on ordinary login");
  }
  process.exit(0);
})().catch((error) => {
  fs.writeFileSync(
    path.join(OUT, "login-vis-error.txt"),
    `${error && error.message}\n${error && error.stack}\n`,
    "utf8"
  );
  process.exit(1);
});
