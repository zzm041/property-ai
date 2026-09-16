Page({
  data: { items: [], loading: false, error: "" },
  onShow() {
    const { withLoading } = require("../../utils/ui");
    withLoading(this, () =>
      require("../../services/api")
        .listWorkOrders()
        .then((data) => this.setData({ items: (data && data.items) || [] }))
    ).catch(() => {});
  },
  retry() {
    this.onShow();
  },
  open(e) {
    wx.navigateTo({ url: `/pages/work-orders/detail?id=${e.currentTarget.dataset.id}` });
  },
});
