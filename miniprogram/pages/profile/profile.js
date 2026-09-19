Page({
  data: { displayName: "", loading: false, error: "" },
  onShow() {
    const me = getApp().globalData.me || {};
    this.setData({ displayName: me.display_name || "" });
  },
  onName(e) {
    this.setData({ displayName: e.detail.value });
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
  back() {
    wx.navigateBack({ delta: 1 });
  },
});
