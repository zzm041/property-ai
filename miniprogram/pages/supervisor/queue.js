Page({
  data: {
    items: [],
    staff: [],
    error: "",
    propertyId: "",
    tenantId: "",
    employeeId: "",
    selectedId: "",
    recommendations: [],
    loading: false,
    nextCursor: "",
  },
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
    this._loadQueue({ reset: true });
  },
  loadMore() {
    if (!this.data.nextCursor) {
      return;
    }
    this._loadQueue({ reset: false });
  },
  _loadQueue({ reset }) {
    const { withLoading } = require("../../utils/ui");
    const api = require("../../services/api");
    const propertyId = this.data.propertyId;
    const afterId = reset ? 0 : this.data.nextCursor;
    withLoading(this, () =>
      Promise.all([
        api.supervisorQueue(propertyId, afterId),
        api.staffDaily(propertyId, this.data.tenantId),
      ]).then(([queue, daily]) => {
        const incoming = (queue && queue.items) || [];
        this.setData({
          items: reset ? incoming : this.data.items.concat(incoming),
          staff: (daily && daily.items) || [],
          nextCursor: (queue && queue.next_cursor) || "",
        });
      })
    ).catch(() => {});
  },
  select(e) {
    this.setData({ selectedId: e.currentTarget.dataset.id, recommendations: [] });
  },
  selectEmployee(e) {
    this.setData({ employeeId: String(e.currentTarget.dataset.id) });
  },
  recommend() {
    if (!this.data.selectedId) {
      this.setData({ error: "请先选择工单" });
      return;
    }
    const { withLoading } = require("../../utils/ui");
    const api = require("../../services/api");
    withLoading(this, () =>
      api.analyzeWorkOrder(this.data.selectedId).then(() =>
        api.workOrderRecommendations(this.data.selectedId).then((data) =>
          this.setData({ recommendations: (data && data.items) || [] })
        )
      )
    ).catch(() => {});
  },
  assign() {
    this._dispatch("assignWorkOrder");
  },
  reassign() {
    this._dispatch("reassignWorkOrder");
  },
  _dispatch(name) {
    if (!this.data.selectedId || !this.data.employeeId) {
      this.setData({ error: "请选择工单和在岗员工" });
      return;
    }
    const { withLoading } = require("../../utils/ui");
    withLoading(this, () =>
      require("../../services/api")[name](this.data.selectedId, {
        employee_id: Number(this.data.employeeId),
      }).then(() => this.load())
    ).catch(() => {});
  },
});
