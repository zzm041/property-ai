Page({
  data: { items: [], propertyId: "", loading: false, error: "" },
  onLoad(query) {
    if (query.propertyId) {
      this.setData({ propertyId: query.propertyId });
    }
  },
  onShow() {
    if (this.data.propertyId) {
      this.load();
    }
  },
  onProperty(e) {
    this.setData({ propertyId: e.detail.value });
  },
  load() {
    const { withLoading } = require("../../utils/ui");
    withLoading(this, () =>
      require("../../services/api")
        .announcements(this.data.propertyId)
        .then((data) => this.setData({ items: (data && data.items) || [] }))
    ).catch(() => {});
  },
  open(e) {
    const id = e.currentTarget.dataset.id;
    wx.navigateTo({
      url: `/pages/announcements/detail?id=${id}&propertyId=${this.data.propertyId}`,
    });
  },
});
