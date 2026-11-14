Page({
  data: { items: [], properties: [], propertyIndex: 0, error: "", propertyId: "", tenantId: "", loading: false, rejectReason: "", nextCursor: 0 },
  onLoad(query) {
    this.requestedPropertyId = query.propertyId || "";
    this.requestedTenantId = query.tenantId || "";
    this.setData({ propertyId: this.requestedPropertyId, tenantId: this.requestedTenantId });
  },
  onShow() {
    const api = require("../../services/api");
    const scope = require("../../utils/property-scope");
    return require("../../utils/ui").withLoading(this, () =>
      scope.authorizedProperties(api, "resident_binding:review").then((properties) => {
        if (!properties.length) throw new Error("当前身份没有绑定审核范围");
        const propertyIndex = scope.preferredIndex(properties, this.requestedPropertyId);
        const property = properties[propertyIndex];
        this.setData({ properties, propertyIndex, propertyId: String(property.id), tenantId: String(property.tenant_id) });
        return this.loadItems();
      })
    ).catch(() => {});
  },
  onProperty(e) {
    if (this.data.loading) return;
    this.setData({ propertyId: e.detail.value, items: [], nextCursor: 0, rejectReason: "" });
  },
  onPropertyChange(e) {
    if (this.data.loading) return;
    const propertyIndex = Number(e.detail.value);
    const property = this.data.properties[propertyIndex];
    if (!property) return;
    this.setData({ propertyIndex, propertyId: String(property.id), tenantId: String(property.tenant_id), items: [], nextCursor: 0, rejectReason: "" });
    return this.load();
  },
  onRejectReason(e) {
    if (!this.data.loading) this.setData({ rejectReason: e.detail.value });
  },
  loadItems(afterId = 0) {
    return require("../../services/api")
      .bindRequests(this.data.propertyId, this.data.tenantId, afterId, 50)
      .then((data) => {
        const labels = require("../../utils/business-labels");
        const decorated = {
          ...data,
          items: ((data && data.items) || []).map((item) => {
            const reviewReady = Number.isSafeInteger(Number(item.user_id)) && Number(item.user_id) > 0 &&
              [item.applicant_display_name, item.property_name, item.building_name, item.unit_name, item.room_no]
                .every((value) => typeof value === "string" && value.trim().length > 0);
            return {
              ...item,
              statusText: item.status === "PENDING" ? "待审核" : item.status === "APPROVED" ? "已通过" : item.status === "REJECTED" ? "已拒绝" : "状态待确认",
              relationText: labels.relationType(item.relation_type),
              reviewReady,
              applicantLabel: reviewReady ? `${item.applicant_display_name}（账号 #${item.user_id}）` : "",
              houseLabel: reviewReady ? [item.property_name, item.building_name, item.unit_name, item.room_no].join(" · ") : "",
            };
          }),
        };
        const result = require("../../utils/paging").appendPage(afterId ? this.data.items : [], decorated, afterId, 50);
        this.setData({ items: result.items, nextCursor: result.nextCursor });
      });
  },
  loadMore() {
    if (this.data.loading || !this.data.nextCursor) return;
    const after = this.data.nextCursor;
    return require("../../utils/ui").withLoading(this, () => this.loadItems(after)).catch(() => {});
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
    if (this.data.loading) return;
    const item = this.data.items.find((entry) => Number(entry.id) === Number(id) && entry.status === "PENDING");
    if (!item) return;
    if (!item.reviewReady) {
      this.setData({ error: "审核资料不完整，暂不能操作" });
      return;
    }
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
