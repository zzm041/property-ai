"use strict";
const automator = require(process.env.MP_AUTOMATOR_MODULE);

function wait(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

(async () => {
  const mp = await automator.connect({ wsEndpoint: process.env.MP_AUTOMATOR_WS });
  const page = await mp.currentPage();
  console.log("path", page.path, "mode", (await page.data()).mode);
  const buttons = await page.$$("button");
  for (const b of buttons) {
    const t = ((await b.text()) || "").trim();
    if (t === "获取验证码") {
      console.log("tapping send");
      await b.tap();
      console.log("tapped");
    }
  }
  await wait(1000);
  const data = await page.data();
  console.log("after", {
    prompt: data.challengePrompt,
    error: data.error,
    loading: data.loading,
    cooldown: data.cooldown,
  });
  process.exit(0);
})().catch((e) => {
  console.error("ERR", e.message);
  process.exit(1);
});
