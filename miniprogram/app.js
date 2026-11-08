const { BASE_URL } = require("./config/env");
const auth = require("./services/auth");

App({
  globalData: {
    baseUrl: BASE_URL,
    me: null,
  },
  onLaunch() {
    const token = auth.getToken();
    if (!token) {
      wx.reLaunch({ url: "/pages/login/login" });
    }
  },
});
