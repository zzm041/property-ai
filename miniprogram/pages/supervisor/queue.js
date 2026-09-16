Page({
  data: {
    items: [],
    error: "",
    propertyId: "",
    employeeId: "",
    selectedId: "",
    recommendations: [],
    loading: false,
  },
  onLoad(query) {
    if (query.propertyId) {
      this.setData({ propertyId: query.propertyId });
    }
  },
  onProperty(e) {
    this.setData({ propertyId: e.detail.value });
  },
  onEmployee(e) {
    this.setData({ employeeId: e.detail.value });
  },
  load() {
    const { withLoading } = require("../../utils/ui");
    withLoading(this, () =>
      require("../../services/api")
        .supervisorQueue(this.data.propertyId)
        .then((data) => this.setData({ items: (data && data.items) || [] }))
    ).catch(() => {});
  },
  select(e) {
    this.setData({ selectedId: e.currentTarget.dataset.id, recommendations: [] });
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
      this.setData({ error: "请填写工单和员工 ID" });
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
