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
    queueView: "dispatch",
    canCancel: false,
    cancelConfirm: false,
    cancelReason: "",
    success: "",
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
    if (this._cancelBusy) return;
    this._queueGeneration = (this._queueGeneration || 0) + 1;
    this._clearSelection();
    this.setData({ propertyId: e.detail.value, items: [], staff: [], nextCursor: "", targetWorkOrderId: 0, loading: false, success: "" });
  },
  changeView(e) {
    if (this.data.loading || this._cancelBusy) return;
    const view = e.currentTarget.dataset.view;
    if (!["dispatch", "unstarted"].includes(view) || view === this.data.queueView) return;
    this._clearSelection();
    this.setData({ queueView: view, targetWorkOrderId: 0, items: [], nextCursor: "", success: "" });
    return this.load();
  },
  load() {
    if (this.data.loading || this._cancelBusy) return Promise.resolve();
    return this._loadQueue({ reset: true });
  },
  loadMore() {
    if (!this.data.nextCursor || this.data.loading || this._cancelBusy) {
      return;
    }
    return this._loadQueue({ reset: false });
  },
  _loadQueue({ reset }) {
    const api = require("../../services/api");
    const propertyId = this.data.propertyId;
    const generation = this._queueGeneration = (this._queueGeneration || 0) + 1;
    const afterId = reset ? 0 : this.data.nextCursor;
    if (reset) this._clearSelection();
    this.setData({ loading: true, error: "" });
    const targetPromise =
      reset && this.data.targetWorkOrderId
        ? api.workOrder(this.data.targetWorkOrderId)
        : Promise.resolve(null);
    return Promise.all([
        api.supervisorQueue(propertyId, afterId, this.data.queueView),
        api.staffDaily(propertyId, this.data.tenantId),
        targetPromise,
      ]).then(([queue, daily, targetDetail]) => {
        if (generation !== this._queueGeneration) return;
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
            canCancel: this._cancellable(target.status),
          });
        }
        this.setData(updates);
      }).catch((error) => {
      if (generation === this._queueGeneration) this.setData({ error: error.message || "请求失败" });
    }).finally(() => {
      if (generation === this._queueGeneration) this.setData({ loading: false });
    });
  },
  select(e) {
    if (this.data.loading || this._cancelBusy) return;
    const selectedId = Number(e.currentTarget.dataset.id);
    const selected = this.data.items.find((item) => item.id === selectedId);
    this.setData({
      selectedId,
      selectedStatus: selected ? selected.status : "",
      selectedProcessReason: selected ? selected.process_reason : "",
      selectedVersion: selected ? selected.version : "",
      recommendations: [],
      decisionReason: "",
      canCancel: Boolean(selected && this._cancellable(selected.status)),
      cancelConfirm: false,
      cancelReason: "",
      success: "",
    });
  },
  _cancellable(status) {
    // Display hint only. The existing service/FSM owns permission and state rules.
    return ["PENDING_ANALYSIS", "PENDING_ASSIGNMENT", "ASSIGNED", "ACCEPTED"].includes(status);
  },
  onCancelReason(e) {
    if (!this._cancelBusy) this.setData({ cancelReason: e.detail.value });
  },
  askCancel() {
    if (this.data.loading || this._cancelBusy || !this.data.selectedId || !this.data.canCancel) return;
    this.setData({ cancelConfirm: true, error: "", success: "" });
  },
  dismissCancel() {
    if (!this._cancelBusy) this.setData({ cancelConfirm: false });
  },
  cancelSelected() {
    if (this.data.loading || this._cancelBusy || !this.data.cancelConfirm || !this.data.canCancel) return;
    const id = Number(this.data.selectedId), version = Number(this.data.selectedVersion);
    if (!Number.isSafeInteger(id) || id < 1 || !Number.isSafeInteger(version) || version < 1) return;
    const reason = this.data.cancelReason.trim();
    this._cancelBusy = true;
    this.setData({ loading: true, error: "", success: "" });
    return require("../../services/api").cancelWorkOrder(id, { version, reason: reason || null })
      .then(async () => {
        // A completed mutation is never offered again if its following refresh fails.
        this._clearSelection();
        this.setData({ targetWorkOrderId: 0, success: `工单 ${id} 已撤销` });
        await this._loadQueue({ reset: true });
      })
      .catch((error) => {
        this.setData({ error: error.message || "撤销失败，请刷新核对工单状态", cancelConfirm: false });
      })
      .finally(() => { this._cancelBusy = false; this.setData({ loading: false }); });
  },
  selectEmployee(e) {
    this.setData({ employeeId: String(e.currentTarget.dataset.id) });
  },
  recommend() {
    if (this.data.loading || this._cancelBusy) return;
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
    if (this.data.loading || this._cancelBusy) return;
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
          return this._loadQueue({ reset: true });
        })
    ).catch(() => {});
  },
  _dispatch(name) {
    if (this.data.loading || this._cancelBusy) return;
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
        return this._loadQueue({ reset: true });
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
      canCancel: false,
      cancelConfirm: false,
      cancelReason: "",
    });
  },
});
