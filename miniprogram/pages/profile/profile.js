function maskPhone(value) {
  let digits = String(value || "").replace(/\D/g, "");
  if (/^861[3-9]\d{9}$/.test(digits)) {
    digits = digits.slice(2);
  }
  if (digits.length < 7) {
    return "****";
  }
  return `${digits.slice(0, 3)}****${digits.slice(-4)}`;
}

Page({
  data: {
    displayName: "",
    oldPhone: "",
    newPhone: "",
    oldCode: "",
    newCode: "",
    oldChallengeId: "",
    oldChallengePrompt: "",
    oldChallengeAnswer: "",
    newChallengeId: "",
    newChallengePrompt: "",
    newChallengeAnswer: "",
    phoneNotice: "",
    wechatPhone: "",
    wechatCode: "",
    wechatChallengeId: "",
    wechatChallengePrompt: "",
    wechatChallengeAnswer: "",
    wechatNotice: "",
    loading: false,
    error: "",
  },
  onShow() {
    const me = getApp().globalData.me || {};
    this.setData({ displayName: me.display_name || "" });
  },
  onName(e) {
    this.setData({ displayName: e.detail.value });
  },
  onPhoneField(e) {
    const field = e.currentTarget.dataset.field;
    const value = e.detail.value;
    const updates = { [field]: value, error: "" };
    if (field === "oldPhone") {
      Object.assign(updates, {
        oldCode: "",
        oldChallengeId: "",
        oldChallengePrompt: "",
        oldChallengeAnswer: "",
      });
    }
    if (field === "newPhone") {
      Object.assign(updates, {
        newCode: "",
        newChallengeId: "",
        newChallengePrompt: "",
        newChallengeAnswer: "",
      });
    }
    this.setData(updates);
  },
  sendPhoneCode(e) {
    const kind = e.currentTarget.dataset.kind;
    const prefix = kind === "old" ? "old" : "new";
    const phone = this.data[`${prefix}Phone`];
    const challengeId = this.data[`${prefix}ChallengeId`];
    const challengeAnswer = this.data[`${prefix}ChallengeAnswer`];
    const api = require("../../services/api");
    const { withLoading } = require("../../utils/ui");
    withLoading(this, async () => {
      if (!challengeId) {
        const challenge = await api.issueChallenge(phone, "login");
        this.setData({
          [`${prefix}ChallengeId`]: challenge.challenge_id,
          [`${prefix}ChallengePrompt`]: challenge.prompt,
          phoneNotice: "请完成安全挑战后再次获取验证码",
        });
        return;
      }
      if (!challengeAnswer) {
        throw new Error("请填写安全挑战答案");
      }
      await api.sendSms(
        phone,
        "change_phone",
        challengeId,
        challengeAnswer
      );
      this.setData({
        [`${prefix}ChallengeId`]: "",
        [`${prefix}ChallengePrompt`]: "",
        [`${prefix}ChallengeAnswer`]: "",
        phoneNotice: `${kind === "old" ? "旧" : "新"}手机号验证码已发送`,
      });
    }).catch(() => {});
  },
  changePhone() {
    const { withLoading } = require("../../utils/ui");
    withLoading(this, async () => {
      const oldPhone = this.data.oldPhone;
      const newPhone = this.data.newPhone;
      const token = await require("../../services/api").changePhone({
        old_phone: oldPhone,
        old_code: this.data.oldCode,
        new_phone: newPhone,
        new_code: this.data.newCode,
      });
      const auth = require("../../services/auth");
      auth.setToken(token.access_token);
      auth.setRefreshToken(token.refresh_token);
      const displayName =
        this.data.displayName === maskPhone(oldPhone)
          ? maskPhone(newPhone)
          : this.data.displayName;
      const app = getApp();
      if (app.globalData.me) {
        app.globalData.me.display_name = displayName;
      }
      this.setData({
        displayName,
        oldPhone: "",
        newPhone: "",
        oldCode: "",
        newCode: "",
        phoneNotice: "手机号已变更，其他旧会话已失效",
      });
      wx.showToast({ title: "手机号已变更", icon: "success" });
    }).catch(() => {});
  },
  save() {
    const { withLoading } = require("../../utils/ui");
    withLoading(this, () =>
      require("../../services/api")
        .updateProfile(this.data.displayName)
        .then((me) => {
          getApp().globalData.me = me;
          wx.showToast({ title: "已保存" });
        })
    ).catch(() => {});
  },
  bindWechat() {
    const { withLoading } = require("../../utils/ui");
    withLoading(this, async () => {
      const auth = require("../../services/auth");
      const code = await auth.login();
      await require("../../services/api").wechatBind(code);
      wx.showToast({ title: "已绑定" });
    }).catch(() => {});
  },
  onWechatField(e) {
    const field = e.currentTarget.dataset.field;
    const updates = { [field]: e.detail.value, error: "" };
    if (field === "wechatPhone") {
      Object.assign(updates, {
        wechatCode: "",
        wechatChallengeId: "",
        wechatChallengePrompt: "",
        wechatChallengeAnswer: "",
      });
    }
    this.setData(updates);
  },
  sendWechatCode() {
    const api = require("../../services/api");
    const { withLoading } = require("../../utils/ui");
    withLoading(this, async () => {
      if (!this.data.wechatChallengeId) {
        const challenge = await api.issueChallenge(
          this.data.wechatPhone,
          "login"
        );
        this.setData({
          wechatChallengeId: challenge.challenge_id,
          wechatChallengePrompt: challenge.prompt,
          wechatNotice: "请完成安全挑战后再次获取验证码",
        });
        return;
      }
      if (!this.data.wechatChallengeAnswer) {
        throw new Error("请填写安全挑战答案");
      }
      await api.sendSms(
        this.data.wechatPhone,
        "login",
        this.data.wechatChallengeId,
        this.data.wechatChallengeAnswer
      );
      this.setData({
        wechatChallengeId: "",
        wechatChallengePrompt: "",
        wechatChallengeAnswer: "",
        wechatNotice: "当前手机号验证码已发送",
      });
    }).catch(() => {});
  },
  rebindWechat() {
    const { withLoading } = require("../../utils/ui");
    withLoading(this, async () => {
      const auth = require("../../services/auth");
      const code = await auth.login();
      const token = await require("../../services/api").wechatRebind({
        code,
        phone: this.data.wechatPhone,
        sms_code: this.data.wechatCode,
      });
      auth.setToken(token.access_token);
      auth.setRefreshToken(token.refresh_token);
      this.setData({
        wechatPhone: "",
        wechatCode: "",
        wechatChallengeId: "",
        wechatChallengePrompt: "",
        wechatChallengeAnswer: "",
        wechatNotice: "微信已安全更换，其他旧会话已失效",
      });
      wx.showToast({ title: "微信已更换", icon: "success" });
    }).catch(() => {});
  },
  back() {
    wx.navigateBack({ delta: 1 });
  },
  goClaim() {
    wx.navigateTo({ url: "/pages/employee-claim/employee-claim" });
  },
});
