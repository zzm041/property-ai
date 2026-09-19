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
  console.log("connected");
  try {
    await mp.callWxMethod("clearStorageSync");
    console.log("cleared storage");
  } catch (error) {
    console.log("clear failed", error.message);
    await mp.evaluate(() => wx.clearStorageSync());
  }
  const page = await mp.currentPage();
  console.log("path", page.path);
  const data = await page.data();
  console.log("mode", data.mode, "phone", Boolean(data.phone));
  process.exit(0);
})().catch((e) => {
  console.error(e.message);
  process.exit(1);
});
