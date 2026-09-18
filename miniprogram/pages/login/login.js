const env = require("../../config/env");

Page({
  data: {
    error: "",
    loading: false,
    localPreview: false,
    identities: [],
    baseUrl: "",
    providerNote: "",
  },
  onLoad() {
    const auth = require("../../services/auth");
    if (auth.getToken()) {
      wx.reLaunch({ url: "/pages/home/home" });
      return;
    }
    this.setData({
      localPreview: env.LOCAL_PREVIEW,
      identities: env.LOCAL_IDENTITIES,
      baseUrl: env.BASE_URL || "未配置",
      providerNote: env.PROVIDER_NOTE,
    });
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
  loginLocal(event) {
    const seedKey = event.currentTarget.dataset.seed;
    if (!seedKey || !env.LOCAL_PREVIEW) {
      this.setData({ error: "本地联调登录未启用" });
      return;
    }
    const { withLoading } = require("../../utils/ui");
    withLoading(this, async () => {
      const auth = require("../../services/auth");
      const api = require("../../services/api");
      auth.clearSession();
      const token = await api.devToken(seedKey);
      auth.setToken(token.access_token);
      getApp().globalData.me = await api.me();
      wx.reLaunch({ url: "/pages/home/home" });
    }).catch(() => {});
  },
});
