Page({
  data: { items: [], loading: false, error: "" },
  onShow() {
    const { withLoading } = require("../../utils/ui");
    withLoading(this, () =>
      require("../../services/api")
        .staffWorkOrders()
        .then((data) => this.setData({ items: (data && data.items) || [] }))
    ).catch(() => {});
  },
  open(e) {
    wx.navigateTo({ url: `/pages/staff-orders/detail?id=${e.currentTarget.dataset.id}` });
  },
});
