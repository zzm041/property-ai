"use strict";
const automator = require(process.env.MP_AUTOMATOR_MODULE);
(async () => {
  const ws = process.env.MP_AUTOMATOR_WS || "ws://127.0.0.1:9431";
  console.log("connecting", ws);
  const mp = await automator.connect({ wsEndpoint: ws });
  console.log("connected");
  try {
    const page = await mp.currentPage();
    console.log("path", page && page.path);
  } catch (error) {
    console.log("currentPage failed", error.message);
  }
  process.exit(0);
})().catch((error) => {
  console.error(error.message);
  process.exit(1);
});
