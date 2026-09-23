Page({
  data: { items: [], error: "", propertyId: "", tenantId: "", loading: false, rejectReason: "" },
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
    if (this.data.loading) return;
    this.setData({ propertyId: e.detail.value });
  },
  onRejectReason(e) {
    if (!this.data.loading) this.setData({ rejectReason: e.detail.value });
  },
  loadItems() {
    return require("../../services/api")
      .bindRequests(this.data.propertyId, this.data.tenantId)
      .then((data) => this.setData({ items: (data && data.items) || [] }));
  },
  load() {
    if (this.data.loading) return Promise.resolve();
    const { withLoading } = require("../../utils/ui");
    return withLoading(this, () => this.loadItems()).catch(() => {});
  },
  approve(e) {
    return this.review(e.currentTarget.dataset.id, false);
  },
  reject(e) {
    return this.review(e.currentTarget.dataset.id, true);
  },
  review(id, reject) {
    if (this.data.loading || !this.data.items.some((item) => Number(item.id) === Number(id) && item.status === "PENDING")) return;
    const reason = this.data.rejectReason.trim();
    if (reject && (!reason || reason.length > 500)) {
      this.setData({ error: "请输入1至500字的驳回原因" });
      return;
    }
    const api = require("../../services/api");
    return require("../../utils/ui").withLoading(this, () =>
      (reject ? api.rejectBindRequest(id, reason) : api.approveBindRequest(id))
        .then(() => { this.setData({ rejectReason: "" }); return this.loadItems(); })
    ).catch(() => {});
  },
});
