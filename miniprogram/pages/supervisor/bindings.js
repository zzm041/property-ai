Page({
  data: { items: [], error: "", propertyId: "", loading: false },
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
        .bindRequests(this.data.propertyId)
        .then((data) => this.setData({ items: (data && data.items) || [] }))
    ).catch(() => {});
  },
  approve(e) {
    require("../../services/api")
      .approveBindRequest(e.currentTarget.dataset.id)
      .then(() => this.load())
      .catch((error) => this.setData({ error: error.message }));
  },
  reject(e) {
    require("../../services/api")
      .rejectBindRequest(e.currentTarget.dataset.id, "不符合入住材料")
      .then(() => this.load())
      .catch((error) => this.setData({ error: error.message }));
  },
});
