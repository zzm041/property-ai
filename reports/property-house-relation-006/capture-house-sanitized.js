"use strict";

const fs = require("fs");
const path = require("path");
const automator = require(process.env.MP_AUTOMATOR_MODULE);

const OUT = path.resolve(__dirname);
const evidence = JSON.parse(fs.readFileSync(path.join(OUT, "page-evidence.json"), "utf8"));

function wait(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

(async () => {
  const miniProgram = await automator.connect({
    wsEndpoint: process.env.MP_AUTOMATOR_WS || "ws://127.0.0.1:9435",
  });
  const page = await miniProgram.currentPage();
  if (!page || page.path !== "pages/supervisor/properties") {
    throw new Error(`property page is not current: ${page && page.path}`);
  }
  const state = await page.data();
  const roomNo = evidence.created_synthetic_codes.room_no;
  const house = (state.houses || []).find((item) => item.room_no === roomNo);
  if (!house) throw new Error("UI-created synthetic house is not present");
  const card = await page.$(`.house-card[data-id="${house.id}"]`);
  const offset = await card.offset();
  await miniProgram.callWxMethod("pageScrollTo", {
    scrollTop: Math.max(0, Number(offset.top) - 500),
    duration: 0,
  });
  await wait(500);
  await miniProgram.screenshot({ path: path.join(OUT, "02-created-house.png") });
  await miniProgram.disconnect();
  console.log(JSON.stringify({ screenshot: "02-created-house.png", resident_cards_visible: false }));
})().catch((error) => {
  console.error(error && error.message);
  process.exit(1);
});
