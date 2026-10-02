Page({
  data: { properties: [], propertyIndex: 0, propertyId: "", snapshot: null, loading: false, error: "" },
  onLoad(query) {
    this.requestedPropertyId = query.propertyId || "";
  },
  onShow() {
    const api = require("../../services/api");
    const scope = require("../../utils/property-scope");
    return require("../../utils/ui").withLoading(this, () =>
      scope.authorizedProperties(api, "attendance:check_in").then((properties) => {
        if (!properties.length) throw new Error("当前身份没有可签到的小区");
        const propertyIndex = scope.preferredIndex(properties, this.requestedPropertyId);
        this.setData({ properties, propertyIndex, propertyId: String(properties[propertyIndex].id) });
      })
    ).catch(() => {});
  },
  onPropertyChange(e) {
    const propertyIndex = Number(e.detail.value);
    const property = this.data.properties[propertyIndex];
    if (property) this.setData({ propertyIndex, propertyId: String(property.id), snapshot: null });
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
