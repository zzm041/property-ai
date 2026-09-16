const TOKEN_KEY = "access_token";

function getToken() {
  return wx.getStorageSync(TOKEN_KEY) || "";
}

function setToken(token) {
  wx.setStorageSync(TOKEN_KEY, token);
}

function clearToken() {
  wx.removeStorageSync(TOKEN_KEY);
}

function login() {
  return new Promise((resolve, reject) => {
    wx.login({
      success: (result) => resolve(result.code),
      fail: reject,
    });
  });
}

module.exports = {
  getToken,
  setToken,
  clearToken,
  login,
};
