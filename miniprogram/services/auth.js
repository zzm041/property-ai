const TOKEN_KEY = "access_token";
const HOUSE_KEY = "selected_house_id";
const WORK_ORDER_KEY = "last_work_order_id";

function getToken() {
  return wx.getStorageSync(TOKEN_KEY) || "";
}

function setToken(token) {
  wx.setStorageSync(TOKEN_KEY, token);
}

function clearToken() {
  wx.removeStorageSync(TOKEN_KEY);
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

function login() {
  return new Promise((resolve, reject) => {
    wx.login({
      success: (result) => resolve(result.code),
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
  clearToken,
  clearSession,
  setSelectedHouseId,
  getSelectedHouseId,
  setLastWorkOrderId,
  getLastWorkOrderId,
  login,
};
