const { strength, meetsCurrentPolicy } = require("../../utils/password");

Page({
  data: {
    step: 1,
    phone: "",
    challengePrompt: "",
    challengeId: "",
    challengeAnswer: "",
    smsCode: "",
    resetToken: "",
    newPassword: "",
    confirmPassword: "",
    showPassword: false,
    strength: "未输入",
    loading: false,
    error: "",
  },
  onLoad(query) {
    if (query && query.phone) {
      this.setData({ phone: decodeURIComponent(query.phone) });
    }
  },
  onPhone(e) {
    const phone = e.detail.value;
    if (phone !== this.data.phone) {
      this.setData({
        phone,
        smsCode: "",
        challengeId: "",
        challengePrompt: "",
        challengeAnswer: "",
        error: "",
      });
    }
  },
  onSmsCode(e) {
    this.setData({ smsCode: e.detail.value });
  },
  onChallengeAnswer(e) {
    this.setData({ challengeAnswer: e.detail.value });
  },
  onNewPassword(e) {
    const value = e.detail.value;
    this.setData({ newPassword: value, strength: strength(value) });
  },
  onConfirm(e) {
    this.setData({ confirmPassword: e.detail.value });
  },
  togglePassword() {
    this.setData({ showPassword: !this.data.showPassword });
  },
  sendSms() {
    const { withLoading } = require("../../utils/ui");
    const api = require("../../services/api");
    withLoading(this, async () => {
      if (!this.data.challengeId) {
        const data = await api.issueChallenge(this.data.phone, "reset");
        this.setData({
          challengeId: data.challenge_id,
          challengePrompt: data.prompt,
          challengeAnswer: "",
          error: "请完成安全挑战后再获取验证码",
        });
        return;
      }
      const result = await api.sendSms(
        this.data.phone,
        "reset",
        this.data.challengeId,
        this.data.challengeAnswer
      );
      this.setData({
        challengeId: "",
        challengePrompt: "",
        challengeAnswer: "",
        error: result && result.delivery_channel === "development"
          ? "开发短信验证码已生成，请按当前可用方式获取。"
          : "短信发送请求已受理，请查收验证码；未收到请勿反复提交。",
      });
    }).catch(() => {});
  },
  verify() {
    const { withLoading } = require("../../utils/ui");
    withLoading(this, async () => {
      const data = await require("../../services/api").verifyPasswordReset(
        this.data.phone,
        this.data.smsCode
      );
      this.setData({ resetToken: data.reset_token, step: 2, error: "" });
    }).catch(() => {});
  },
  confirm() {
    if (this.data.newPassword !== this.data.confirmPassword) {
      this.setData({ error: "两次密码不一致" });
      return;
    }
    if (!meetsCurrentPolicy(this.data.newPassword)) {
      this.setData({ error: "新密码不符合当前策略" });
      return;
    }
    const { withLoading } = require("../../utils/ui");
    withLoading(this, async () => {
      await require("../../services/api").confirmPasswordReset(
        this.data.resetToken,
        this.data.newPassword,
        this.data.confirmPassword
      );
      require("../../services/auth").clearSession();
      wx.showToast({ title: "请重新登录" });
      wx.redirectTo({ url: "/pages/login/login" });
    }).catch(() => {});
  },
  back() {
    wx.redirectTo({ url: "/pages/login/login" });
  },
});
