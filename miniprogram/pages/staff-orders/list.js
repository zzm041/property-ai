Page({
  data: { items: [], loading: false, error: "" },
  onShow() {
    const { withLoading } = require("../../utils/ui");
    withLoading(this, () =>
      require("../../services/api")
        .staffWorkOrders()
        .then((data) => {
          const { workOrderStatus } = require("../../utils/business-labels");
          const items = ((data && data.items) || []).map((item) => ({
            ...item,
            statusLabel: workOrderStatus(item.status),
          }));
          this.setData({ items });
        })
    ).catch(() => {});
  },
  open(e) {
    wx.navigateTo({ url: `/pages/staff-orders/detail?id=${e.currentTarget.dataset.id}` });
  },
});
