Page({
  data: { items: [], properties: [], propertyIndex: 0, propertyId: "", loading: false, error: "" },
  onLoad(query) {
    this.requestedPropertyId = query.propertyId || "";
  },
  onShow() {
    const api = require("../../services/api");
    const scope = require("../../utils/property-scope");
    return require("../../utils/ui").withLoading(this, () =>
      scope.authorizedProperties(api, "announcement:read").then((properties) => {
        if (!properties.length) throw new Error("当前身份没有可查看公告的小区");
        const propertyIndex = scope.preferredIndex(properties, this.requestedPropertyId);
        this.setData({ properties, propertyIndex, propertyId: String(properties[propertyIndex].id) });
        return this.loadItems();
      })
    ).catch(() => {});
  },
  onPropertyChange(e) {
    const propertyIndex = Number(e.detail.value);
    const property = this.data.properties[propertyIndex];
    if (!property) return;
    this.setData({ propertyIndex, propertyId: String(property.id), items: [] });
    return this.load();
  },
  load() {
    const { withLoading } = require("../../utils/ui");
    return withLoading(this, () => this.loadItems()).catch(() => {});
  },
  loadItems() {
    return require("../../services/api")
        .announcements(this.data.propertyId)
        .then((data) => {
          const label = require("../../utils/business-labels").announcementStatus;
          this.setData({
            items: ((data && data.items) || []).map((item) => ({
              ...item,
              statusText: label(item.status),
            })),
          });
        });
  },
  open(e) {
    const id = e.currentTarget.dataset.id;
    wx.navigateTo({
      url: `/pages/announcements/detail?id=${id}&propertyId=${this.data.propertyId}`,
    });
  },
});
