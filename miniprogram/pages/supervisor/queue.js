Page({
  data: {
    items: [],
    staff: [],
    error: "",
    propertyId: "",
    tenantId: "",
    employeeId: "",
    selectedId: "",
    selectedStatus: "",
    selectedProcessReason: "",
    selectedVersion: "",
    targetWorkOrderId: 0,
    decisionReason: "",
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
    const workOrderId = Number(query.workOrderId || 0);
    if (Number.isInteger(workOrderId) && workOrderId > 0) {
      this.setData({ targetWorkOrderId: workOrderId });
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
    const targetPromise =
      reset && this.data.targetWorkOrderId
        ? api.workOrder(this.data.targetWorkOrderId)
        : Promise.resolve(null);
    withLoading(this, () =>
      Promise.all([
        api.supervisorQueue(propertyId, afterId),
        api.staffDaily(propertyId, this.data.tenantId),
        targetPromise,
      ]).then(([queue, daily, targetDetail]) => {
        const incoming = (queue && queue.items) || [];
        let items = reset ? incoming : this.data.items.concat(incoming);
        const target = targetDetail
          ? require("../../utils/ui").workOrderOf(targetDetail)
          : null;
        if (target) {
          if (
            Number(target.property_id) !== Number(propertyId) ||
            (this.data.tenantId &&
              Number(target.tenant_id) !== Number(this.data.tenantId))
          ) {
            throw new Error("通知与工单管理范围不一致");
          }
          if (!items.some((item) => Number(item.id) === Number(target.id))) {
            items = [target].concat(items);
          }
        }
        const updates = {
          items,
          staff: (daily && daily.items) || [],
          nextCursor: (queue && queue.next_cursor) || "",
        };
        if (target) {
          Object.assign(updates, {
            selectedId: target.id,
            selectedStatus: target.status,
            selectedProcessReason: target.process_reason,
            selectedVersion: target.version,
            recommendations: [],
            decisionReason: "",
          });
        }
        this.setData(updates);
      })
    ).catch(() => {});
  },
  select(e) {
    const selectedId = Number(e.currentTarget.dataset.id);
    const selected = this.data.items.find((item) => item.id === selectedId);
    this.setData({
      selectedId,
      selectedStatus: selected ? selected.status : "",
      selectedProcessReason: selected ? selected.process_reason : "",
      selectedVersion: selected ? selected.version : "",
      recommendations: [],
      decisionReason: "",
    });
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
  onDecisionReason(e) {
    this.setData({ decisionReason: e.detail.value });
  },
  decide(e) {
    const decision = e.currentTarget.dataset.decision;
    const reason = this.data.decisionReason.trim();
    if (!this.data.selectedId || this.data.selectedStatus !== "PROCESSING") {
      this.setData({ error: "请先选择异常处理中的工单" });
      return;
    }
    if (decision === "CONTINUE" && !reason) {
      this.setData({ error: "继续处理必须填写方案说明" });
      return;
    }
    const { withLoading } = require("../../utils/ui");
    withLoading(this, () =>
      require("../../services/api")
        .decideWorkOrder(this.data.selectedId, {
          decision,
          reason: reason || null,
          version: Number(this.data.selectedVersion),
        })
        .then(() => {
          this._clearSelection();
          this.load();
        })
    ).catch(() => {});
  },
  _dispatch(name) {
    if (!this.data.selectedId || !this.data.employeeId) {
      this.setData({ error: "请选择工单和在岗员工" });
      return;
    }
    const reason = this.data.decisionReason.trim();
    if (name === "reassignWorkOrder" && !reason) {
      this.setData({ error: "改派必须填写原因" });
      return;
    }
    const { withLoading } = require("../../utils/ui");
    withLoading(this, () =>
      require("../../services/api")[name](this.data.selectedId, {
        employee_id: Number(this.data.employeeId),
        reason: reason || null,
        version: Number(this.data.selectedVersion),
      }).then(() => {
        this._clearSelection();
        this.load();
      })
    ).catch(() => {});
  },
  _clearSelection() {
    this.setData({
      selectedId: "",
      selectedStatus: "",
      selectedProcessReason: "",
      selectedVersion: "",
      employeeId: "",
      decisionReason: "",
      recommendations: [],
    });
  },
});
