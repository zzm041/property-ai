Page({
  data: { items: [], propertyId: "", error: "" },
  onProperty(e) { this.setData({ propertyId: e.detail.value }); },
  load() {
    require("../../services/api").request({
      path: `/announcements?property_id=${this.data.propertyId}`,
    }).then((data) => this.setData({ items: data.items || [] }))
      .catch((error) => this.setData({ error: error.message }));
  },
  open(e) {
    wx.navigateTo({
      url: `/pages/announcements/detail?title=${e.currentTarget.dataset.title}&content=${e.currentTarget.dataset.content}`,
    });
  },
});
