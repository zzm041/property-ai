Page({
  data: { token: "", loading: false, error: "" },
  onToken(e) {
    this.setData({ token: e.detail.value });
  },
  claim() {
    if (!this.data.token) {
      this.setData({ error: "请输入邀请令牌" });
      return;
    }
    const { withLoading } = require("../../utils/ui");
    withLoading(this, () =>
      require("../../services/api")
        .claimEmployee(this.data.token)
        .then(() => {
          wx.showToast({ title: "认领成功" });
          wx.reLaunch({ url: "/pages/home/home" });
        })
    ).catch(() => {});
  },
});
