"use strict";
const automator = require(process.env.MP_AUTOMATOR_MODULE);
function wait(ms) {
  return new Promise((r) => setTimeout(r, ms));
}
(async () => {
  const mp = await automator.connect({ wsEndpoint: process.env.MP_AUTOMATOR_WS });
  const page = await mp.currentPage();
  console.log("path", page.path);
  const buttons = await page.$$("button");
  console.log("buttons", buttons.length);
  for (const b of buttons) {
    console.log("btn", ((await b.text()) || "").trim());
  }
  process.exit(0);
})().catch((e) => {
  console.error(e.message);
  process.exit(1);
});
