Page({
  data: { error: "", loading: false },
  onLoad() {
    if (require("../../services/auth").getToken()) {
      wx.reLaunch({ url: "/pages/home/home" });
    }
  },
  async login() {
    const { withLoading } = require("../../utils/ui");
    withLoading(this, async () => {
      const auth = require("../../services/auth");
      const api = require("../../services/api");
      const code = await auth.login();
      const token = await api.wechatLogin(code);
      auth.setToken(token.access_token);
      getApp().globalData.me = await api.me();
      wx.reLaunch({ url: "/pages/home/home" });
    }).catch(() => {});
  },
});
