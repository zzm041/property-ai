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
    phone: "",
    mode: "password",
    password: "",
    showPassword: false,
    smsCode: "",
    challengePrompt: "",
    challengeId: "",
    challengeAnswer: "",
    cooldown: 0,
  },
  onLoad() {
    const auth = require("../../services/auth");
    if (auth.getToken()) {
      wx.reLaunch({ url: "/pages/home/home" });
      return;
    }
  },
  onUnload() {
    if (this._timer) {
      clearInterval(this._timer);
    }
  },
  onPhone(e) {
    const phone = e.detail.value;
    if (phone !== this.data.phone) {
      this.setData({
        phone,
        smsCode: "",
        challengePrompt: "",
        challengeId: "",
        challengeAnswer: "",
        error: "",
      });
    }
  },
  onPassword(e) {
    this.setData({ password: e.detail.value });
  },
  onSmsCode(e) {
    this.setData({ smsCode: e.detail.value });
  },
  onChallengeAnswer(e) {
    this.setData({ challengeAnswer: e.detail.value });
  },
  togglePassword() {
    this.setData({ showPassword: !this.data.showPassword });
  },
  toggleMode() {
    const next = this.data.mode === "password" ? "sms" : "password";
    this.setData({
      mode: next,
      password: "",
      smsCode: "",
      challengePrompt: "",
      challengeId: "",
      challengeAnswer: "",
      error: "",
      showPassword: false,
    });
  },
  goReset() {
    const phone = this.data.phone ? `?phone=${encodeURIComponent(this.data.phone)}` : "";
    wx.navigateTo({ url: `/pages/password-reset/password-reset${phone}` });
  },
  async afterLogin() {
    const api = require("../../services/api");
    getApp().globalData.me = await api.me();
    wx.reLaunch({ url: "/pages/home/home" });
  },
  startCooldown(seconds) {
    if (this._timer) {
      clearInterval(this._timer);
    }
    let left = seconds;
    this.setData({ cooldown: left });
    this._timer = setInterval(() => {
      left -= 1;
      if (left <= 0) {
        clearInterval(this._timer);
        this.setData({ cooldown: 0 });
        return;
      }
      this.setData({ cooldown: left });
    }, 1000);
  },
  async ensureChallenge() {
    const api = require("../../services/api");
    if (this.data.challengeId && this.data.challengePrompt) {
      return;
    }
    const data = await api.issueChallenge(this.data.phone, "login");
    this.setData({
      challengeId: data.challenge_id,
      challengePrompt: data.prompt,
      challengeAnswer: "",
    });
  },
  sendSms() {
    const { withLoading } = require("../../utils/ui");
    withLoading(this, async () => {
      await this.ensureChallenge();
      if (!this.data.challengeAnswer) {
        this.setData({ error: "请先完成安全挑战" });
        return;
      }
      const data = await require("../../services/api").sendSms(
        this.data.phone,
        "login",
        this.data.challengeId,
        this.data.challengeAnswer
      );
      this.setData({
        challengeId: "",
        challengePrompt: "",
        challengeAnswer: "",
        error:
          data && data.delivery_channel === "development"
            ? "开发短信验证码已生成，请按当前可用方式获取。"
            : "短信发送请求已受理，请查收验证码；未收到请勿反复提交。",
      });
      this.startCooldown(data && data.expires_in ? Math.min(60, data.expires_in) : 60);
    }).catch(() => {});
  },
  submit() {
    if (this.data.mode === "password") {
      this.loginPassword();
      return;
    }
    this.loginSms();
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
});
