Page({
  data: { items: [], propertyId: "", loading: false, error: "" },
  onLoad(query) {
    if (query.propertyId) {
      this.setData({ propertyId: query.propertyId });
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
    const title = encodeURIComponent(e.currentTarget.dataset.title || "");
    const content = encodeURIComponent(e.currentTarget.dataset.content || "");
    wx.navigateTo({
      url: `/pages/announcements/detail?title=${title}&content=${content}`,
    });
  },
});
