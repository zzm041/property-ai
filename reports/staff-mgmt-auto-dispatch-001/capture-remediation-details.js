"use strict";

const path = require("path");
const automator = require(process.env.MP_AUTOMATOR_MODULE);

const OUT = path.resolve(__dirname);
const WS = process.env.MP_AUTOMATOR_WS || "ws://127.0.0.1:9435";

function wait(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function captureSelected(miniProgram, page, name) {
  const cards = await page.$$("view.card");
  const detail = cards[cards.length - 1];
  const offset = await detail.offset();
  await miniProgram.callWxMethod("pageScrollTo", {
    scrollTop: Math.max(0, Number(offset.top) - 16),
    duration: 0,
  });
  await wait(500);
  await miniProgram.screenshot({ path: path.join(OUT, name) });
}

(async () => {
  const miniProgram = await automator.connect({ wsEndpoint: WS });
  const page = await miniProgram.currentPage();
  if (!page || page.path !== "pages/supervisor/staff") {
    throw new Error(`staff page is not current: ${page && page.path}`);
  }
  const state = await page.data();
  if (Number(state.selectedEmployeeId) === 9) {
    await captureSelected(miniProgram, page, "fix1-ended-recovered-detail.png");
    const primary = await page.$('.employee-card[data-id="10"]');
    if (!primary) {
      throw new Error("primary employee card missing");
    }
    await primary.tap();
    for (let attempt = 0; attempt < 20; attempt += 1) {
      if (Number((await page.data()).selectedEmployeeId) === 10) {
        break;
      }
      await wait(150);
    }
  }
  if (Number((await page.data()).selectedEmployeeId) !== 10) {
    throw new Error("primary employee selection did not update");
  }
  await wait(1000);
  await captureSelected(miniProgram, page, "fix1-primary-maintained-detail.png");
  await miniProgram.disconnect();
  console.log(JSON.stringify({ ended_employee_id: 9, primary_employee_id: 10 }));
})().catch((error) => {
  console.error(error && error.message);
  process.exit(1);
});
