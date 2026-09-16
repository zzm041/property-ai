Page({
  data: { items: [], error: "" },
  onShow() {
    const api = require("../../services/api");
    const ids = wx.getStorageSync("work_order_ids") || [];
    Promise.all(ids.map((id) => api.workOrder(id).catch(() => null)))
      .then((items) => this.setData({ items: items.filter(Boolean) }))
      .catch((error) => this.setData({ error: error.message }));
  },
  open(e) {
    wx.navigateTo({ url: `/pages/work-orders/detail?id=${e.currentTarget.dataset.id}` });
  },
});
