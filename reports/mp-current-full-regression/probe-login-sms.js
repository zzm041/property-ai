"use strict";
const automator = require(process.env.MP_AUTOMATOR_MODULE);
const http = require("http");
const fs = require("fs");
const path = require("path");
const ACCOUNTS = JSON.parse(fs.readFileSync(path.join(__dirname, "accounts.local.json"), "utf8"));

function wait(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

function postJson(body) {
  return new Promise((resolve, reject) => {
    const data = JSON.stringify(body);
    const req = http.request(
      {
        host: "127.0.0.1",
        port: 8083,
        path: "/api/v1/auth/sms/test-inbox",
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Request-Id": "probe-inbox",
          "Content-Length": Buffer.byteLength(data),
        },
      },
      (res) => {
        let raw = "";
        res.on("data", (c) => {
          raw += c;
        });
        res.on("end", () => resolve(JSON.parse(raw)));
      }
    );
    req.on("error", reject);
    req.write(data);
    req.end();
  });
}

(async () => {
  const mp = await automator.connect({ wsEndpoint: process.env.MP_AUTOMATOR_WS });
  let page = await mp.currentPage();
  let data = await page.data();
  console.log("prompt", data.challengePrompt);
  const inputs = await page.$$("input");
  for (const el of inputs) {
    const ph = await el.attribute("placeholder");
    if (ph === "挑战答案") {
      await el.input("16");
      console.log("filled answer 16 for 7+9");
    }
  }
  const buttons = await page.$$("button");
  for (const b of buttons) {
    if (((await b.text()) || "").trim() === "获取验证码") {
      await b.tap();
      console.log("second send");
    }
  }
  await wait(1000);
  page = await mp.currentPage();
  data = await page.data();
  console.log("after send", { error: data.error, cooldown: data.cooldown });
  const inbox = await postJson({ phone: ACCOUNTS.phones.staff2, purpose: "login" });
  const code = inbox.data && inbox.data.code;
  console.log("inbox", Boolean(code));
  const inputs2 = await page.$$("input");
  for (const el of inputs2) {
    const ph = await el.attribute("placeholder");
    if (ph === "短信验证码") {
      await el.input(code);
      console.log("filled code");
    }
  }
  const buttons2 = await page.$$("button");
  for (const b of buttons2) {
    if (((await b.text()) || "").trim() === "登录") {
      await b.tap();
      console.log("tapped login");
    }
  }
  for (let i = 0; i < 20; i += 1) {
    await wait(400);
    page = await mp.currentPage();
    if (page.path && page.path.indexOf("home") >= 0) {
      console.log("home ok");
      process.exit(0);
    }
  }
  console.log("last", page.path);
  process.exit(2);
})().catch((e) => {
  console.error("ERR", e.message);
  process.exit(1);
});
