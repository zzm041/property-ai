const env = require("../../config/env");

function applySession(tokenPayload) {
  const auth = require("../../services/auth");
  auth.setToken(tokenPayload.access_token);
  if (tokenPayload.refresh_token) {
    auth.setRefreshToken(tokenPayload.refresh_token);
  }
}

Page({
  data: { allowed: false, identities: [], error: "", loading: false },
  onLoad() {
    if (!env.LOCAL_PREVIEW) {
      this.setData({ allowed: false, error: "当前环境不允许调试登录。" });
      return;
    }
    this.setData({ allowed: true, identities: env.LOCAL_IDENTITIES });
  },
  loginLocal(event) {
    const seedKey = event.currentTarget.dataset.seed;
    if (!seedKey || !env.LOCAL_PREVIEW) {
      this.setData({ error: "当前环境不允许调试登录。" });
      return;
    }
    const { withLoading } = require("../../utils/ui");
    withLoading(this, async () => {
      const auth = require("../../services/auth");
      const api = require("../../services/api");
      auth.clearSession();
      const token = await api.devToken(seedKey);
      applySession(token);
      getApp().globalData.me = await api.me();
      wx.reLaunch({ url: "/pages/home/home" });
    }).catch(() => {});
  },
});
