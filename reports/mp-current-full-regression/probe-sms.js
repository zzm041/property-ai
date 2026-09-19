"use strict";
const automator = require(process.env.MP_AUTOMATOR_MODULE);
const http = require("http");
const fs = require("fs");
const path = require("path");
const ACCOUNTS = JSON.parse(fs.readFileSync(path.join(__dirname, "accounts.local.json"), "utf8"));

function wait(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

(async () => {
  const mp = await automator.connect({ wsEndpoint: process.env.MP_AUTOMATOR_WS });
  const page = await mp.currentPage();
  console.log("1 path", page.path);
  const links = await page.$$(".link-text");
  console.log("2 links", links.length);
  for (const node of links) {
    const text = ((await node.text()) || "").trim();
    console.log(" link", text);
    if (text.indexOf("验证码登录") >= 0) {
      await node.tap();
      console.log("3 tapped sms");
    }
  }
  await wait(500);
  const inputs = await page.$$("input");
  console.log("4 inputs", inputs.length);
  for (const el of inputs) {
    const ph = await el.attribute("placeholder");
    console.log(" ph", ph);
    if (ph === "手机号") {
      await el.input(ACCOUNTS.phones.staff2);
      console.log("5 filled phone");
    }
  }
  const buttons = await page.$$("button");
  for (const b of buttons) {
    const t = ((await b.text()) || "").trim();
    console.log(" btn", t);
  }
  process.exit(0);
})().catch((e) => {
  console.error("ERR", e.message);
  process.exit(1);
});
