Page({
  data: { items: [], error: "" },
  onShow() {
    require("../../services/api").staffWorkOrders()
      .then((data) => this.setData({ items: data.items || [] }))
      .catch((error) => this.setData({ error: error.message }));
  },
  open(e) {
    wx.navigateTo({ url: `/pages/staff-orders/detail?id=${e.currentTarget.dataset.id}` });
  },
});
