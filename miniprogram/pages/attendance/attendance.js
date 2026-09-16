Page({
  data: { propertyId: "", snapshot: null, loading: false, error: "" },
  onLoad(query) {
    if (query.propertyId) {
      this.setData({ propertyId: query.propertyId });
    }
  },
  onProperty(e) {
    this.setData({ propertyId: e.detail.value });
  },
  checkIn() {
    const { withLoading } = require("../../utils/ui");
    withLoading(this, () =>
      require("../../services/api")
        .checkIn(Number(this.data.propertyId))
        .then((snapshot) => this.setData({ snapshot }))
    ).catch(() => {});
  },
  checkOut() {
    const { withLoading } = require("../../utils/ui");
    withLoading(this, () =>
      require("../../services/api")
        .checkOut(Number(this.data.propertyId))
        .then((snapshot) => this.setData({ snapshot }))
    ).catch(() => {});
  },
});
