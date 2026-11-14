function decorateWorkOrder(item, labels) {
  return item
    ? {
        ...item,
        statusLabel: labels.workOrderStatus(item.status),
        reasonLabel: labels.workOrderReason(item.process_reason),
      }
    : null;
}

Page({
  data: {
    items: [],
    properties: [],
    propertyIndex: 0,
    staff: [],
    error: "",
    propertyId: "",
    tenantId: "",
    employeeId: "",
    selectedId: "",
    selectedStatus: "",
    selectedStatusLabel: "",
    selectedProcessReason: "",
    selectedProcessReasonLabel: "",
    selectedVersion: "",
    targetWorkOrderId: 0,
    decisionReason: "",
    recommendations: [],
    diagnosis: null,
    diagnosisLoading: false,
    diagnosisError: "",
    agentAvailable: false,
    agentResult: null,
    agentLoading: false,
    agentError: "",
    internalHistory: null,
    internalHistoryLoading: false,
    internalHistoryError: "",
    loading: false,
    nextCursor: "",
    queueView: "dispatch",
    canCancel: false,
    cancelConfirm: false,
    cancelReason: "",
    success: "",
  },
  onLoad(query) {
    this.requestedPropertyId = query.propertyId || "";
    this.requestedTenantId = query.tenantId || "";
    this.setData({ propertyId: this.requestedPropertyId, tenantId: this.requestedTenantId });
    const workOrderId = Number(query.workOrderId || 0);
    if (Number.isInteger(workOrderId) && workOrderId > 0) {
      this.setData({ targetWorkOrderId: workOrderId });
    }
  },
  onShow() {
    this._hidden = false;
    const generation = this.clearScope();
    const api = require("../../services/api");
    const scope = require("../../utils/property-scope");
    this.setData({ loading: true });
    return Promise.resolve().then(() =>
      scope.authorizedProperties(api, "work_order:dispatch").then((properties) => {
        if (generation !== this._viewGeneration) return;
        if (!properties.length) throw new Error("当前身份没有工单调度范围");
        const propertyIndex = scope.preferredIndex(properties, this.requestedPropertyId);
        const property = properties[propertyIndex];
        this.setData({ properties, propertyIndex, propertyId: String(property.id), tenantId: String(property.tenant_id) });
        return this._loadQueue({ reset: true });
      })
    ).catch(error => {
      if (generation === this._viewGeneration) this.setData({ error: error.message || "调度范围读取失败" });
    }).finally(() => {
      if (generation === this._viewGeneration) this.setData({ loading: false });
    });
  },
  clearScope() {
    this._viewGeneration = (this._viewGeneration || 0) + 1;
    this._queueGeneration = (this._queueGeneration || 0) + 1;
    this._clearSelection();
    this.setData({ items: [], staff: [], properties: [], propertyId: "", tenantId: "",
      nextCursor: "", error: "", success: "", loading: false, agentAvailable: false });
    return this._viewGeneration;
  },
  _withCurrentView(work) {
    const generation = this._viewGeneration;
    const current = () => !this._hidden && generation === this._viewGeneration;
    const page = { setData: values => { if (current()) this.setData(values); } };
    return require("../../utils/ui").withLoading(page, () => current() ? work(current) : undefined);
  },
  clearDiagnosis() {
    this.clearAgent();
    this.clearInternalHistory();
    this._diagnosisVersion = (this._diagnosisVersion || 0) + 1;
    this.setData({ diagnosis: null, diagnosisLoading: false, diagnosisError: "" });
    return this._diagnosisVersion;
  },
  clearAgent() {
    this._agentGeneration = (this._agentGeneration || 0) + 1;
    this.setData({ agentResult: null, agentLoading: false, agentError: "" });
    return this._agentGeneration;
  },
  reviewQueue() {
    if (this._hidden || this.data.loading || this.data.agentLoading || !this.data.agentAvailable || this.data.queueView !== 'dispatch') return Promise.resolve();
    const propertyId = Number(this.data.propertyId);
    if (!Number.isSafeInteger(propertyId) || propertyId <= 0) return Promise.resolve();
    const generation = this.clearAgent();
    const queueGeneration = this._queueGeneration;
    const current = () => !this._hidden && generation === this._agentGeneration &&
      queueGeneration === this._queueGeneration && propertyId === Number(this.data.propertyId);
    this.setData({ agentLoading: true });
    return require('../../services/api').supervisorAgent(propertyId, '请根据当前待派和异常队列，给出需要优先核实的工单及依据。').then(result => {
      if (!current()) return;
      if (!result || result.status !== 'completed') throw new Error('暂时无法生成跟进建议，请继续使用工单诊断。');
      if (Number(result.property_id) !== propertyId || !Array.isArray(result.items)) throw new Error('范围已变化，请刷新队列后重试。');
      for (const item of result.items) {
        const visible = this.data.items.find(row => Number(row.id) === Number(item.work_order_id));
        if (!visible || Number(visible.version) !== Number(item.version)) throw new Error('工单已变化，请刷新队列后重试。');
      }
      this.setData({ agentResult: result });
    }).catch(error => {
      if (current()) this.setData({ agentResult: null, agentError: error.code === 'AGENT.DISABLED'
        ? '当前未启用跟进建议，可继续查看工单诊断。' : (error.message || '跟进建议读取失败') });
    }).finally(() => {
      if (current()) this.setData({ agentLoading: false });
    });
  },
  clearInternalHistory() {
    this._historyGeneration = (this._historyGeneration || 0) + 1;
    this.setData({ internalHistory: null, internalHistoryLoading: false, internalHistoryError: "" });
    return this._historyGeneration;
  },
  loadInternalHistory() {
    const generation = this.clearInternalHistory();
    const id = Number(this.data.selectedId), propertyId = Number(this.data.propertyId), version = Number(this.data.selectedVersion);
    if (![id, propertyId, version].every(value => Number.isSafeInteger(value) && value > 0)) {
      this.setData({ internalHistoryError: "请先选择有效工单" });
      return Promise.resolve();
    }
    this.setData({ internalHistoryLoading: true });
    return require("../../services/api").workOrder(id, "property").then(item => {
      if (generation !== this._historyGeneration) return;
      const work = item && item.work_order;
      if (!work || Number(work.id) !== id || Number(work.property_id) !== propertyId || Number(work.version) !== version) {
        throw new Error("工单已变化，请刷新队列后查看记录");
      }
      const display = require("../../utils/work-order-display").presentDetail(item);
      this.setData({ internalHistory: { logs: display.logs, notes: item.extra_notes || "",
        reportText: item.text || "", asrText: item.asr_text || "" } });
    }).catch(error => {
      if (generation === this._historyGeneration) this.setData({ internalHistoryError: error.message || "内部记录读取失败" });
    }).finally(() => {
      if (generation === this._historyGeneration) this.setData({ internalHistoryLoading: false });
    });
  },
  onHide() { this._hidden = true; this.clearScope(); },
  onUnload() { this._hidden = true; this.clearScope(); },
  diagnose() {
    const generation = this.clearDiagnosis();
    const id = Number(this.data.selectedId), propertyId = Number(this.data.propertyId), version = Number(this.data.selectedVersion);
    if (![id, propertyId, version].every(value => Number.isSafeInteger(value) && value > 0)) {
      this.setData({ diagnosisError: "请先选择有效工单" });
      return Promise.resolve();
    }
    this.setData({ diagnosisLoading: true });
    return require("../../services/api").workOrderDiagnosis(id, propertyId, version)
      .then(data => {
        if (generation !== this._diagnosisVersion) return;
        if (!data || Number(data.work_order_id) !== id || Number(data.property_id) !== propertyId || Number(data.version) !== version) {
          throw new Error("诊断与当前选择不一致，请刷新工单");
        }
        this.setData({ diagnosis: { ...data, facts: (data.facts || []).map(fact => ({ ...fact, recordedAtLabel: require("../../utils/date-time").formatBeijingTime(fact.recorded_at) })) } });
      }).catch(error => {
        if (generation === this._diagnosisVersion) this.setData({ diagnosisError: error.message || "诊断读取失败" });
      }).finally(() => {
        if (generation === this._diagnosisVersion) this.setData({ diagnosisLoading: false });
      });
  },
  onProperty(e) {
    if (this._cancelBusy) return;
    this._queueGeneration = (this._queueGeneration || 0) + 1;
    this._clearSelection();
    this.setData({ propertyId: e.detail.value, items: [], staff: [], nextCursor: "", targetWorkOrderId: 0, loading: false, success: "" });
  },
  onPropertyChange(e) {
    if (this.data.loading || this._cancelBusy) return;
    const propertyIndex = Number(e.detail.value);
    const property = this.data.properties[propertyIndex];
    if (!property) return;
    this._queueGeneration = (this._queueGeneration || 0) + 1;
    this._clearSelection();
    this.setData({ propertyIndex, propertyId: String(property.id), tenantId: String(property.tenant_id), items: [], staff: [], nextCursor: "", targetWorkOrderId: 0, loading: false, success: "" });
    return this.load();
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
    if (this._hidden) return Promise.resolve();
    const api = require("../../services/api");
    const propertyId = this.data.propertyId;
    const generation = this._queueGeneration = (this._queueGeneration || 0) + 1;
    const afterId = reset ? 0 : this.data.nextCursor;
    this.clearAgent();
    if (reset) this._clearSelection();
    this.setData({ agentAvailable: false });
    this.setData({ loading: true, error: "" });
    const targetPromise =
      reset && this.data.targetWorkOrderId
        ? api.workOrder(this.data.targetWorkOrderId, "property")
        : Promise.resolve(null);
    return Promise.all([
        api.supervisorQueue(propertyId, afterId, this.data.queueView),
        api.staffDaily(propertyId, this.data.tenantId),
        typeof api.employees === "function"
          ? api
              .employees(propertyId, this.data.tenantId, 0, false)
              .catch(() => ({ items: [] }))
          : Promise.resolve({ items: [] }),
        targetPromise,
        typeof api.supervisorAgentCapabilities === 'function'
          ? api.supervisorAgentCapabilities(propertyId).catch(() => ({ enabled: false }))
          : Promise.resolve({ enabled: false }),
      ]).then(([queue, daily, employees, targetDetail, agentCapability]) => {
        if (generation !== this._queueGeneration) return;
        const labels = require("../../utils/business-labels");
        const employeeNumbers = {};
        ((employees && employees.items) || []).forEach((detail) => {
          if (detail && detail.employee) {
            employeeNumbers[detail.employee.id] = detail.employee.employee_no;
          }
        });
        const incoming = ((queue && queue.items) || []).map((item) =>
          decorateWorkOrder(item, labels)
        );
        let items = reset ? incoming : this.data.items.concat(incoming);
        const target = targetDetail
          ? decorateWorkOrder(require("../../utils/ui").workOrderOf(targetDetail), labels)
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
          agentAvailable: Boolean(agentCapability && agentCapability.enabled),
          staff: ((daily && daily.items) || []).map((item, index) => ({
            ...item,
            dutyLabel: labels.onDutyStatus(item.on_duty),
            employeeLabel: employeeNumbers[item.employee_id]
              ? `员工编号 ${employeeNumbers[item.employee_id]}`
              : `处理人员 ${index + 1}`,
          })),
          nextCursor: (queue && queue.next_cursor) || "",
        };
        if (target) {
          Object.assign(updates, {
            selectedId: target.id,
            selectedStatus: target.status,
            selectedStatusLabel: labels.workOrderStatus(target.status),
            selectedProcessReason: target.process_reason,
            selectedProcessReasonLabel: labels.workOrderReason(target.process_reason),
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
    this.clearDiagnosis();
    if (this.data.loading || this._cancelBusy) return;
    const selectedId = Number(e.currentTarget.dataset.id);
    const selected = this.data.items.find((item) => item.id === selectedId);
    this.setData({
      selectedId,
      selectedStatus: selected ? selected.status : "",
      selectedStatusLabel: selected ? selected.statusLabel : "",
      selectedProcessReason: selected ? selected.process_reason : "",
      selectedProcessReasonLabel: selected ? selected.reasonLabel : "",
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
    const generation = this._viewGeneration;
    const current = () => !this._hidden && generation === this._viewGeneration;
    this.clearDiagnosis();
    this._cancelBusy = true;
    this.setData({ loading: true, error: "", success: "" });
    return require("../../services/api").cancelWorkOrder(id, { version, reason: reason || null })
      .then(async () => {
        if (!current()) return;
        // A completed mutation is never offered again if its following refresh fails.
        this._clearSelection();
        this.setData({ targetWorkOrderId: 0, success: `工单 ${id} 已撤销` });
        await this._loadQueue({ reset: true });
      })
      .catch((error) => {
        if (current()) this.setData({ error: error.message || "撤销失败，请刷新核对工单状态", cancelConfirm: false });
      })
      .finally(() => { this._cancelBusy = false; if (current()) this.setData({ loading: false }); });
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
    this.clearDiagnosis();
    const api = require("../../services/api");
    const id = this.data.selectedId;
    this._withCurrentView(current =>
      api.analyzeWorkOrder(id).then(() => current() ?
        api.workOrderRecommendations(id).then((data) =>
          current() && this.setData({
            recommendations: ((data && data.items) || []).map((item) => ({
              ...item,
              employeeLabel:
                (this.data.staff.find(
                  (staff) => Number(staff.employee_id) === Number(item.employee_id)
                ) || {}).employeeLabel || "处理人员",
              filterReasonText: item.eligible
                ? "符合派单条件"
                : require("../../utils/business-labels").dispatchFilterReason(item.filter_reason),
            })),
          })
        ) : undefined
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
    this.clearDiagnosis();
    this._withCurrentView(current =>
      require("../../services/api")
        .decideWorkOrder(this.data.selectedId, {
          decision,
          reason: reason || null,
          version: Number(this.data.selectedVersion),
        })
        .then(() => {
          if (!current()) return;
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
    this.clearDiagnosis();
    this._withCurrentView(current =>
      require("../../services/api")[name](this.data.selectedId, {
        employee_id: Number(this.data.employeeId),
        reason: reason || null,
        version: Number(this.data.selectedVersion),
      }).then(() => {
        if (!current()) return;
        this._clearSelection();
        return this._loadQueue({ reset: true });
      })
    ).catch(() => {});
  },
  _clearSelection() {
    this.clearDiagnosis();
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
