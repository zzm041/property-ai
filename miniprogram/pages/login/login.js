const env = require("../../config/env");

function applySession(tokenPayload) {
  const auth = require("../../services/auth");
  auth.setToken(tokenPayload.access_token);
  if (tokenPayload.refresh_token) {
    auth.setRefreshToken(tokenPayload.refresh_token);
  }
}

Page({
  data: {
    error: "",
    loading: false,
    localPreview: false,
    identities: [],
    baseUrl: "",
    providerNote: "",
    phone: "",
    smsCode: "",
    password: "",
    showReset: false,
    resetCode: "",
    newPassword: "",
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
  onPhone(e) {
    this.setData({ phone: e.detail.value });
  },
  onSmsCode(e) {
    this.setData({ smsCode: e.detail.value });
  },
  onPassword(e) {
    this.setData({ password: e.detail.value });
  },
  onResetCode(e) {
    this.setData({ resetCode: e.detail.value });
  },
  onNewPassword(e) {
    this.setData({ newPassword: e.detail.value });
  },
  toggleReset() {
    this.setData({ showReset: !this.data.showReset });
  },
  async afterLogin() {
    const api = require("../../services/api");
    getApp().globalData.me = await api.me();
    wx.reLaunch({ url: "/pages/home/home" });
  },
  sendSms() {
    const { withLoading } = require("../../utils/ui");
    withLoading(this, () =>
      require("../../services/api")
        .sendSms(this.data.phone, "login")
        .then((data) => {
          const note = data && data.delivery_channel === "development"
            ? "验证码已由开发短信替身发送，不能作为真实短信验收。"
            : "验证码已发送";
          this.setData({ error: note });
        })
    ).catch(() => {});
  },
  loginSms() {
    const { withLoading } = require("../../utils/ui");
    withLoading(this, async () => {
      const token = await require("../../services/api").smsLogin(
        this.data.phone,
        this.data.smsCode
      );
      applySession(token);
      await this.afterLogin();
    }).catch(() => {});
  },
  loginPassword() {
    const { withLoading } = require("../../utils/ui");
    withLoading(this, async () => {
      const token = await require("../../services/api").passwordLogin(
        this.data.phone,
        this.data.password
      );
      applySession(token);
      await this.afterLogin();
    }).catch(() => {});
  },
  sendResetSms() {
    const { withLoading } = require("../../utils/ui");
    withLoading(this, () =>
      require("../../services/api").sendSms(this.data.phone, "reset")
    ).catch(() => {});
  },
  resetPassword() {
    const { withLoading } = require("../../utils/ui");
    withLoading(this, async () => {
      const token = await require("../../services/api").resetPassword(
        this.data.phone,
        this.data.resetCode,
        this.data.newPassword
      );
      applySession(token);
      await this.afterLogin();
    }).catch(() => {});
  },
  async loginWechat() {
    const { withLoading } = require("../../utils/ui");
    withLoading(this, async () => {
      const auth = require("../../services/auth");
      const api = require("../../services/api");
      const code = await auth.login();
      const token = await api.wechatLogin(code);
      applySession(token);
      await this.afterLogin();
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
      applySession(token);
      await this.afterLogin();
    }).catch(() => {});
  },
});
