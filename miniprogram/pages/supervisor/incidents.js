Page({
  data: { items: [], error: "", propertyId: "", loading: false },
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
        .incidents(this.data.propertyId)
        .then((data) => this.setData({ items: (data && data.items) || [] }))
    ).catch(() => {});
  },
});
