Page({
  data: { items: [], nextCursor: 0, hasMore: false, properties: [], propertyIndex: 0, propertyId: "", loading: false, error: "",
    audience: "resident", audienceIndex: 0, audienceOptions: ["住户公告", "物业内部公告"], canReadInternal: false },
  onLoad(query) {
    this.requestedPropertyId = query.propertyId || "";
    this.requestedAudience = query.audience === "property" ? "property" : "resident";
  },
  onShow() {
    const version = this.clearItems();
    this.scopes = [];
    const audience = this.requestedAudience || "resident";
    this.setData({ properties: [], propertyId: "", audience, audienceIndex: audience === "property" ? 1 : 0, canReadInternal: false, loading: true });
    const api = require("../../services/api");
    const scope = require("../../utils/property-scope");
    return Promise.all([scope.authorizedProperties(api, "announcement:read"), api.me()])
      .then(([properties, me]) => {
        if (version !== this.queryVersion) return;
        if (!properties.length) throw new Error("当前身份没有可查看公告的小区");
        this.scopes = (me && me.scopes) || [];
        const propertyIndex = scope.preferredIndex(properties, this.requestedPropertyId);
        const property = properties[propertyIndex];
        this.setData({ properties, propertyIndex, propertyId: String(property.id), canReadInternal: this.internalScopeAllows(property) });
        return this.loadItems();
      }).catch(error => {
        if (version === this.queryVersion) this.setData({ error: error.message || "公告加载失败", loading: false });
      });
  },
  clearItems() {
    this.queryVersion = (this.queryVersion || 0) + 1;
    this.setData({ items: [], nextCursor: 0, hasMore: false, error: "", loading: false });
    return this.queryVersion;
  },
  onHide() { this.clearItems(); },
  onUnload() { this.clearItems(); },
  internalScopeAllows(property) {
    return (this.scopes || []).some(scope =>
      ["STAFF", "SUPERVISOR", "PROPERTY_ADMIN", "TENANT_ADMIN"].includes(scope.role_code) &&
      require("../../utils/property-scope").scopeAllows(scope, "announcement:read", property));
  },
  onAudienceChange(e) {
    const audienceIndex = Number(e.detail.value) === 1 ? 1 : 0;
    if (audienceIndex === 1 && !this.data.canReadInternal) return;
    this.clearItems();
    this.setData({ audienceIndex, audience: audienceIndex ? "property" : "resident" });
    return this.loadItems();
  },
  onPropertyChange(e) {
    const propertyIndex = Number(e.detail.value);
    const property = this.data.properties[propertyIndex];
    if (!property) return;
    this.clearItems();
    const audience = this.requestedAudience || "resident";
    this.setData({ propertyIndex, propertyId: String(property.id), audience, audienceIndex: audience === "property" ? 1 : 0,
      canReadInternal: this.internalScopeAllows(property) });
    return this.load();
  },
  load() {
    return this.loadItems();
  },
  loadMore() {
    if (this.data.loading || !this.data.hasMore) return Promise.resolve();
    return this.loadItems(true);
  },
  loadItems(append = false) {
    const version = append ? this.queryVersion : this.clearItems();
    const afterId = append ? this.data.nextCursor : 0;
    if (!this.data.propertyId) return Promise.resolve();
    if (this.data.audience === "property" && !this.data.canReadInternal) {
      this.setData({ error: "当前身份没有该小区物业公告的读取权限" });
      return Promise.resolve();
    }
    this.setData({ loading: true });
    return require("../../services/api")
        .announcements(this.data.propertyId, afterId, 50, this.data.audience)
        .then((data) => {
          if (version !== this.queryVersion) return;
          const label = require("../../utils/business-labels").announcementStatus;
          const nextCursor = data && data.next_cursor;
          if (nextCursor != null && (!Number.isSafeInteger(nextCursor) || nextCursor <= afterId)) {
            throw new Error("公告分页异常，请刷新后重试");
          }
          const incoming = ((data && data.items) || []).map((item) => ({ ...item, statusText: label(item.status) }));
          this.setData({
            items: append ? this.data.items.concat(incoming) : incoming,
            nextCursor: nextCursor || 0, hasMore: nextCursor != null,
          });
        }).catch(error => {
          if (version === this.queryVersion) this.setData({ error: error.message || "公告加载失败" });
        }).finally(() => {
          if (version === this.queryVersion) this.setData({ loading: false });
        });
  },
  open(e) {
    const id = e.currentTarget.dataset.id;
    wx.navigateTo({
      url: `/pages/announcements/detail?id=${id}&propertyId=${this.data.propertyId}&audience=${this.data.audience}`,
    });
  },
});
