Page({
  data: { error: "" },
  onLoad() {
    if (require("../../services/auth").getToken()) {
      wx.reLaunch({ url: "/pages/home/home" });
    }
  },
  async login() {
    this.setData({ error: "" });
    try {
      const auth = require("../../services/auth");
      const api = require("../../services/api");
      const code = await auth.login();
      const token = await api.wechatLogin(code);
      auth.setToken(token.access_token);
      getApp().globalData.me = await api.me();
      wx.reLaunch({ url: "/pages/home/home" });
    } catch (error) {
      this.setData({ error: error.message || "登录失败" });
    }
  },
});
