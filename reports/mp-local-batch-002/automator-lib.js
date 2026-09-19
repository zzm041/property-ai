"use strict";

const fs = require("fs");
const path = require("path");

function createToolkit(outDir) {
  function writeJson(name, data) {
    fs.writeFileSync(path.join(outDir, name), JSON.stringify(data, null, 2), "utf8");
  }

  async function screenshot(miniProgram, name) {
    await miniProgram.screenshot({ path: path.join(outDir, name) });
  }

  async function wait(ms) {
    await new Promise((resolve) => setTimeout(resolve, ms));
  }

  async function current(miniProgram) {
    return miniProgram.currentPage();
  }

  async function tapButton(page, label) {
    const buttons = await page.$$("button");
    const labels = [];
    for (const button of buttons) {
      const text = ((await button.text()) || "").trim();
      labels.push(text);
      if (text === label) {
        await button.tap();
        return labels;
      }
    }
    throw new Error(`button not found: ${label}; have ${labels.join("|")}`);
  }

  async function tapViewText(page, selector, expected) {
    const nodes = await page.$$(selector);
    const labels = [];
    for (const node of nodes) {
      const text = ((await node.text()) || "").replace(/\s+/g, " ").trim();
      labels.push(text);
      if (text.indexOf(expected) >= 0) {
        await node.tap();
        return labels;
      }
    }
    throw new Error(`view not found: ${expected}; have ${labels.join("|")}`);
  }

  async function loginAs(miniProgram, label) {
    await miniProgram.evaluate(() => {
      try {
        wx.removeStorageSync("access_token");
        wx.removeStorageSync("selected_house_id");
        wx.removeStorageSync("last_work_order_id");
      } catch (error) {
        // ignore
      }
    });
    await miniProgram.reLaunch("/pages/login/login");
    await wait(1200);
    let page = await current(miniProgram);
    await tapButton(page, label);
    for (let i = 0; i < 20; i += 1) {
      await wait(400);
      page = await current(miniProgram);
      if (page.path && page.path.indexOf("pages/home/home") >= 0) {
        return page;
      }
    }
    throw new Error(`login did not reach home after ${label}`);
  }

  async function waitPath(miniProgram, fragment) {
    let page;
    for (let i = 0; i < 25; i += 1) {
      page = await current(miniProgram);
      if (page.path && page.path.indexOf(fragment) >= 0) {
        return page;
      }
      await wait(400);
    }
    throw new Error(`did not reach ${fragment}; last ${page && page.path}`);
  }

  return {
    writeJson,
    screenshot,
    wait,
    current,
    tapButton,
    tapViewText,
    loginAs,
    waitPath,
  };
}

module.exports = { createToolkit };
