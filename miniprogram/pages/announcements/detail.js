Page({
  data: { title: "", content: "", status: "", error: "", loading: false,
    canReadInternal: false, audienceIndex: 0, audienceOptions: ["住户公告", "物业内部公告"] },
  onLoad(query) {
    this.id = Number(query.id);
    this.propertyId = query.propertyId || "";
    this.audience = query.audience === "property" ? "property" : "resident";
    this.setData({ audienceIndex: this.audience === "property" ? 1 : 0 });
    if (!this.id || !this.propertyId) {
      this.setData({ error: "缺少公告或小区" });
      return;
    }
  },
  onShow() {
    const version = this.clearContent();
    const api = require("../../services/api");
    const scope = require("../../utils/property-scope");
    this.setData({ canReadInternal: false, loading: true });
    return Promise.all([scope.authorizedProperties(api, "announcement:read"), api.me()])
      .then(([properties, me]) => {
        if (version !== this.queryVersion) return;
        const property = properties.find(item => Number(item.id) === Number(this.propertyId));
        const canReadInternal = Boolean(property && ((me && me.scopes) || []).some(item =>
          ["STAFF", "SUPERVISOR", "PROPERTY_ADMIN", "TENANT_ADMIN"].includes(item.role_code) &&
          scope.scopeAllows(item, "announcement:read", property)));
        this.setData({ canReadInternal });
        return this.reload();
      }).catch(error => {
        if (version === this.queryVersion) this.setData({ error: error.message || "公告加载失败", loading: false });
      });
  },
  onAudienceChange(e) {
    const audienceIndex = Number(e.detail.value) === 1 ? 1 : 0;
    if (audienceIndex && !this.data.canReadInternal) return;
    this.audience = audienceIndex ? "property" : "resident";
    this.setData({ audienceIndex });
    return this.reload();
  },
  onHide() { this.clearContent(); },
  onUnload() { this.clearContent(); },
  clearContent() {
    this.queryVersion = (this.queryVersion || 0) + 1;
    this.setData({ title: "", content: "", status: "", error: "", loading: false });
    return this.queryVersion;
  },
  reload() {
    const version = this.clearContent();
    if (!this.id || !this.propertyId) { this.setData({ error: "缺少公告或小区" }); return Promise.resolve(); }
    this.setData({ loading: true });
    return require("../../services/api")
        .announcement(this.propertyId, this.id, this.audience)
        .then((item) => {
          if (version !== this.queryVersion) return;
          this.setData({
            title: item.title || "",
            content: item.content || "",
            status: item.status || "",
          });
        }).catch(error => {
          if (version === this.queryVersion) this.setData({ error: error.message || "公告加载失败" });
        }).finally(() => {
          if (version === this.queryVersion) this.setData({ loading: false });
        });
  },
});
