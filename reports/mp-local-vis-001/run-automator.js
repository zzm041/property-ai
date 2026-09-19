"use strict";

const fs = require("fs");
const path = require("path");
const automator = require(process.env.MP_AUTOMATOR_MODULE);

const OUT = path.resolve(__dirname);
const requests = [];

function writeJson(name, data) {
  fs.writeFileSync(path.join(OUT, name), JSON.stringify(data, null, 2), "utf8");
}

function redactMe(me) {
  if (!me || typeof me !== "object") {
    return me;
  }
  return {
    display_name: me.display_name,
    roles: me.roles,
    permissions: me.permissions,
    scopes: me.scopes,
  };
}

(async () => {
  const miniProgram = await automator.connect({
    wsEndpoint: "ws://127.0.0.1:9420",
  });
  miniProgram.on("exception", (err) => {
    fs.appendFileSync(
      path.join(OUT, "automator-exception.txt"),
      `${err && err.message}\n${err && err.stack}\n`,
      "utf8"
    );
  });

  await miniProgram.exposeFunction("recordMpRequest", (info) => {
    if (!info || !info.url) {
      return;
    }
    const url = String(info.url);
    const keep =
      url.indexOf("/auth/dev/token") >= 0 ||
      /\/me(\?|$)/.test(url.replace(/\/+$/, ""));
    if (!keep) {
      return;
    }
    requests.push({
      method: info.method || "GET",
      path: url.replace(/^https?:\/\/[^/]+/, ""),
      status: info.status,
    });
  });

  await miniProgram.mockWxMethod(
    "request",
    function (opts) {
      const self = this;
      return new Promise((resolve, reject) => {
        self.origin({
          ...opts,
          success(res) {
            if (typeof recordMpRequest === "function") {
              recordMpRequest({
                url: opts.url,
                method: opts.method,
                status: res.statusCode,
              });
            }
            if (opts.success) {
              opts.success(res);
            }
            resolve(res);
          },
          fail(err) {
            if (opts.fail) {
              opts.fail(err);
            }
            reject(err);
          },
        });
      });
    }
  );

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
  await new Promise((resolve) => setTimeout(resolve, 1500));

  let page = await miniProgram.currentPage();
  const loginData = await page.data();
  writeJson("login-page-data.json", {
    path: page.path,
    localPreview: loginData.localPreview,
    baseUrl: loginData.baseUrl,
    identities: loginData.identities,
    error: loginData.error,
  });
  await miniProgram.screenshot({ path: path.join(OUT, "03-login-page.png") });

  const buttons = await page.$$("button");
  let resident;
  const labels = [];
  for (const button of buttons) {
    const text = ((await button.text()) || "").trim();
    labels.push(text);
    if (text === "以住户登录") {
      resident = button;
    }
  }
  writeJson("login-buttons.json", { labels });
  if (!resident) {
    throw new Error(`resident login button missing: ${labels.join("|")}`);
  }
  await resident.tap();

  for (let i = 0; i < 20; i += 1) {
    await new Promise((resolve) => setTimeout(resolve, 500));
    page = await miniProgram.currentPage();
    if (page.path && page.path.indexOf("pages/home/home") >= 0) {
      break;
    }
  }

  page = await miniProgram.currentPage();
  const homeData = await page.data();
  const globalMe = await miniProgram.evaluate(() => {
    try {
      return getApp().globalData.me;
    } catch (error) {
      return null;
    }
  });
  await miniProgram.screenshot({ path: path.join(OUT, "06-resident-home.png") });
  writeJson("home-page-data.json", {
    path: page.path,
    me: redactMe(homeData.me || globalMe),
    canResident: homeData.canResident,
    canStaff: homeData.canStaff,
    canManage: homeData.canManage,
    error: homeData.error,
  });
  writeJson("04-network-requests.json", requests);

  miniProgram.disconnect();
})().catch((error) => {
  fs.writeFileSync(path.join(OUT, "automator-error.txt"), String(error && error.stack || error), "utf8");
  process.exit(1);
});
