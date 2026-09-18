Page({
  data: { items: [], error: "", propertyId: "", tenantId: "", loading: false },
  onLoad(query) {
    if (query.propertyId) {
      this.setData({ propertyId: query.propertyId });
    }
    if (query.tenantId) {
      this.setData({ tenantId: query.tenantId });
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
        .staffDaily(this.data.propertyId, this.data.tenantId)
        .then((data) => this.setData({ items: (data && data.items) || [] }))
    ).catch(() => {});
  },
});
