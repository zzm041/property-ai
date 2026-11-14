const TOKEN_KEY = "access_token";
const HOUSE_KEY = "selected_house_id";
const WORK_ORDER_KEY = "last_work_order_id";
const DEVELOPMENT_WECHAT_DEVICE_KEY = "development_wechat_device_id";

function getToken() {
  return wx.getStorageSync(TOKEN_KEY) || "";
}

function setToken(token) {
  wx.setStorageSync(TOKEN_KEY, token);
}

const REFRESH_KEY = "refresh_token";

function getRefreshToken() {
  return wx.getStorageSync(REFRESH_KEY) || "";
}

function setRefreshToken(token) {
  if (token) {
    wx.setStorageSync(REFRESH_KEY, token);
  } else {
    wx.removeStorageSync(REFRESH_KEY);
  }
}

function clearToken() {
  wx.removeStorageSync(TOKEN_KEY);
  wx.removeStorageSync(REFRESH_KEY);
}

function setSelectedHouseId(houseId) {
  if (houseId) {
    wx.setStorageSync(HOUSE_KEY, String(houseId));
  } else {
    wx.removeStorageSync(HOUSE_KEY);
  }
}

function getSelectedHouseId() {
  return wx.getStorageSync(HOUSE_KEY) || "";
}

function setLastWorkOrderId(id) {
  if (id) {
    wx.setStorageSync(WORK_ORDER_KEY, String(id));
  }
}

function getLastWorkOrderId() {
  return wx.getStorageSync(WORK_ORDER_KEY) || "";
}

function developmentWechatDeviceId() {
  let value = wx.getStorageSync(DEVELOPMENT_WECHAT_DEVICE_KEY) || "";
  if (value) {
    return value;
  }
  const randomPart = () => Math.random().toString(36).slice(2, 13);
  value = `dev_${Date.now().toString(36)}_${randomPart()}_${randomPart()}`;
  wx.setStorageSync(DEVELOPMENT_WECHAT_DEVICE_KEY, value);
  return value;
}

function login() {
  return new Promise((resolve, reject) => {
    wx.login({
      success: (result) => {
        const env = require("../config/env");
        if (env.ENV_VERSION === "develop") {
          resolve(
            `wx-dev-device-v1:${developmentWechatDeviceId()}:${result.code}`
          );
          return;
        }
        resolve(result.code);
      },
      fail: reject,
    });
  });
}

function clearSession() {
  clearToken();
  wx.removeStorageSync(HOUSE_KEY);
  wx.removeStorageSync(WORK_ORDER_KEY);
  try {
    const app = getApp();
    if (app && app.globalData) {
      app.globalData.me = null;
    }
  } catch (error) {
    // App may not exist during early launch.
  }
}

module.exports = {
  getToken,
  setToken,
  getRefreshToken,
  setRefreshToken,
  clearToken,
  clearSession,
  setSelectedHouseId,
  getSelectedHouseId,
  setLastWorkOrderId,
  getLastWorkOrderId,
  login,
};
