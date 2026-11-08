const { formatBeijingTime } = require("../../utils/date-time");
const api = require("../../services/api");
const { withLoading, withMutation, capturePage, isPageCurrent, resetPage } = require("../../utils/page-context");

const CATEGORY_OPTIONS = [
  { value: "PLUMBING", label: "给排水" },
  { value: "ELECTRICAL", label: "电气" },
  { value: "HVAC", label: "暖通" },
  { value: "CLEANING", label: "保洁" },
  { value: "SECURITY", label: "安防" },
  { value: "OTHER", label: "其他" },
];
const PRIORITY_OPTIONS = [
  { value: "LOW", label: "低" },
  { value: "MEDIUM", label: "中" },
  { value: "HIGH", label: "高" },
  { value: "URGENT", label: "紧急" },
];
const STATUS_OPTIONS = [
  { value: "ACTIVE", label: "启用" },
  { value: "DISABLED", label: "禁用并回退" },
];

function scopeAllows(scope, property) {
  if ((scope.permissions || []).indexOf("sla:manage") < 0) {
    return false;
  }
  if (scope.scope_type === "SYSTEM") {
    return true;
  }
  if (Number(scope.tenant_id) !== Number(property.tenant_id)) {
    return false;
  }
  return (
    scope.scope_type === "TENANT" ||
    (scope.scope_type === "PROPERTY" &&
      Number(scope.property_id) === Number(property.id))
  );
}

function tenantScopeAllows(scope, tenantId) {
  return (
    (scope.permissions || []).indexOf("sla:manage") >= 0 &&
    scope.scope_type === "TENANT" &&
    Number(scope.tenant_id) === Number(tenantId)
  );
}

function loadAllProperties(afterId, collected) {
  const current = collected || [];
  return api.catalogProperties(afterId || 0).then((response) => {
    const items = (response && response.items) || [];
    const next = current.concat(items);
    if (items.length < 100) {
      return next;
    }
    const nextCursor = Number(items[items.length - 1].id);
    if (nextCursor <= Number(afterId || 0)) {
      throw new Error("小区分页游标无效，请刷新后重试");
    }
    return loadAllProperties(nextCursor, next);
  });
}

function sourceLabel(source) {
  if (source === "PROPERTY") {
    return "当前小区覆盖";
  }
  if (source === "TENANT") {
    return "企业默认";
  }
  return "系统默认策略";
}

function optionLabel(options, value, fallback) {
  const item = options.find((option) => option.value === value);
  return item ? item.label : fallback;
}

function optionValue(options, index) {
  const item = options[index];
  return item ? item.value : "";
}

Page({
  data: {
    properties: [],
    propertyIndex: 0,
    propertyId: "",
    tenantId: "",
    canTenant: false,
    scopeOptions: [{ value: "PROPERTY", label: "当前小区覆盖" }],
    scopeIndex: 0,
    categoryOptions: CATEGORY_OPTIONS,
    categoryIndex: 0,
    priorityOptions: PRIORITY_OPTIONS,
    priorityIndex: 1,
    statusOptions: STATUS_OPTIONS,
    visibleStatusOptions: STATUS_OPTIONS,
    statusIndex: 0,
    respondMinutes: "",
    completeMinutes: "",
    acceptMinutes: "",
    warningLeadMinutes: "",
    escalationMinutes: "",
    changeReason: "",
    versioningAvailable: false,
    policiesReady: false,
    expectedUpdatedAt: "",
    editingId: "",
    items: [],
    effective: [],
    loading: false,
    error: "",
    notice: "",
  },

  onLoad(query) {
    this.requestedPropertyId = query.propertyId || "";
  },

  onShow() {
    if (this._pendingMutation) { this._hidden = false; this.setData({ loading: true }); return Promise.resolve(); }
    resetPage(this, false, { policiesReady: false, canTenant: false, effective: [], editingId: "", expectedUpdatedAt: "", acceptMinutes: "", respondMinutes: "", completeMinutes: "", warningLeadMinutes: "", escalationMinutes: "", changeReason: "", versioningAvailable: false });
    return this.bootstrap();
  },
  onHide() { resetPage(this, true, { policiesReady: false, canTenant: false, effective: [], editingId: "", expectedUpdatedAt: "", acceptMinutes: "", respondMinutes: "", completeMinutes: "", warningLeadMinutes: "", escalationMinutes: "", changeReason: "", versioningAvailable: false }); },
  onUnload() { this.onHide(); },

  bootstrap() {
    const generation = capturePage(this);
    return withLoading(this, () =>
      Promise.all([api.me(), loadAllProperties()]).then(([me, allProperties]) => {
        if (!isPageCurrent(this, generation)) return;
        const scopes = me.scopes || [];
        const properties = allProperties.filter((property) =>
          scopes.some((scope) => scopeAllows(scope, property))
        );
        if (!properties.length) {
          throw new Error("当前身份没有可配置 SLA 的小区范围");
        }
        let propertyIndex = properties.findIndex(
          (item) => Number(item.id) === Number(this.requestedPropertyId)
        );
        if (propertyIndex < 0) {
          propertyIndex = 0;
        }
        this.meScopes = scopes;
        this.applyProperty(properties, propertyIndex);
        return this.fetchPolicies();
      })
    ).catch(() => {});
  },

  applyProperty(properties, propertyIndex) {
    const property = properties[propertyIndex];
    const canTenant = (this.meScopes || []).some((scope) =>
      tenantScopeAllows(scope, property.tenant_id)
    );
    this.setData({
      properties,
      propertyIndex,
      propertyId: String(property.id),
      tenantId: String(property.tenant_id),
      canTenant,
      scopeOptions: [
        { value: "PROPERTY", label: "当前小区覆盖" },
        ...(canTenant ? [{ value: "TENANT", label: "企业默认" }] : []),
      ],
      scopeIndex: 0,
      visibleStatusOptions: STATUS_OPTIONS,
      items: [],
      effective: [],
      policiesReady: false, versioningAvailable: false, editingId: "", expectedUpdatedAt: "",
      acceptMinutes: "", respondMinutes: "", completeMinutes: "", warningLeadMinutes: "", escalationMinutes: "", changeReason: "",
      notice: "",
    });
  },

  onPropertyChange(event) {
    if (this._hidden || this.data.loading) return;
    const propertyIndex = Number(event.detail.value);
    if (!this.data.properties[propertyIndex]) {
      return;
    }
    this.requestedPropertyId = String(this.data.properties[propertyIndex].id);
    this.applyProperty(this.data.properties, propertyIndex);
    this.load();
  },

  onScopeChange(event) {
    if (this._hidden || this.data.loading) return;
    const scopeIndex = Number(event.detail.value);
    const scope = optionValue(this.data.scopeOptions, scopeIndex);
    this.setData(
      {
        scopeIndex,
        visibleStatusOptions:
          scope === "TENANT" ? [STATUS_OPTIONS[0]] : STATUS_OPTIONS,
        notice: "",
      },
      () => this.syncEditor()
    );
  },

  onCategoryChange(event) {
    if (this._hidden || this.data.loading) return;
    this.setData({ categoryIndex: Number(event.detail.value), notice: "" });
    this.syncEditor();
  },

  onPriorityChange(event) {
    if (this._hidden || this.data.loading) return;
    this.setData({ priorityIndex: Number(event.detail.value), notice: "" });
    this.syncEditor();
  },

  onStatusChange(event) {
    if (this._hidden || this.data.loading) return;
    this.setData({ statusIndex: Number(event.detail.value), notice: "" });
  },

  onField(event) {
    if (this._hidden || this.data.loading) return;
    this.setData({ [event.currentTarget.dataset.field]: event.detail.value });
  },

  fetchPolicies() {
    const generation = capturePage(this);
    this.setData({ policiesReady: false, versioningAvailable: false, items: [], effective: [],
      editingId: "", expectedUpdatedAt: "", acceptMinutes: "", respondMinutes: "", completeMinutes: "",
      warningLeadMinutes: "", escalationMinutes: "", changeReason: "" });
    return api.slaPolicies(this.data.propertyId, this.data.tenantId).then((payload) => {
        if (!isPageCurrent(this, generation)) return;
      const items = (payload.items || []).map((item) => ({
        ...item,
        updatedAtLabel: formatBeijingTime(item.updated_at),
        scopeText: item.property_id ? "当前小区覆盖" : "企业默认",
        categoryText: optionLabel(CATEGORY_OPTIONS, item.category, "其他"),
        priorityText: optionLabel(PRIORITY_OPTIONS, item.priority, "待确认"),
        statusText: optionLabel(STATUS_OPTIONS, item.status, "待确认"),
      }));
      const effective = (payload.effective || []).map((item) => {
        const version = items.find((row) => row.id === item.policy_id);
        if (item.policy_id != null && !version) throw new Error("生效策略版本未完整返回，请刷新后再编辑");
        return {
        ...item,
        accept_minutes: version && version.accept_minutes != null ? version.accept_minutes : item.respond_minutes,
        warning_lead_minutes: version && version.warning_lead_minutes != null ? version.warning_lead_minutes
          : Math.min(item.complete_minutes, Math.max(Math.floor(item.complete_minutes / 4), 15)),
        escalation_minutes: version && version.escalation_minutes != null ? version.escalation_minutes : 0,
        key: `${item.category}-${item.priority}`,
        sourceText: sourceLabel(item.source),
        categoryText: optionLabel(CATEGORY_OPTIONS, item.category, "其他"),
        priorityText: optionLabel(PRIORITY_OPTIONS, item.priority, "待确认"),
      }; });
      this.setData({ items, effective, policiesReady: true, versioningAvailable: !!payload.versioning_available, error: "" });
      this.syncEditor();
    });
  },

  load() {
    if (this._hidden || !this.data.propertyId || this.data.loading) {
      return Promise.resolve();
    }
    return withLoading(this, () => this.fetchPolicies()).catch(() => {});
  },

  syncEditor() {
    const scope = optionValue(this.data.scopeOptions, this.data.scopeIndex);
    const category = optionValue(CATEGORY_OPTIONS, this.data.categoryIndex);
    const priority = optionValue(PRIORITY_OPTIONS, this.data.priorityIndex);
    const exact = this.data.items.find(
      (item) =>
        item.category === category &&
        item.priority === priority &&
        (scope === "TENANT"
          ? item.property_id === null
          : Number(item.property_id) === Number(this.data.propertyId))
    );
    const effective = this.data.effective.find(
      (item) => item.category === category && item.priority === priority
    );
    const source =
      exact ||
      (scope === "PROPERTY" || (effective && effective.source === "CODE_DEFAULT")
        ? effective
        : null);
    const statusIndex =
      scope === "PROPERTY" && exact && exact.status === "DISABLED" ? 1 : 0;
    this.setData({
      editingId: exact ? String(exact.id) : "",
      expectedUpdatedAt: exact ? exact.updated_at : "",
      respondMinutes: source ? String(source.respond_minutes) : "",
      completeMinutes: source ? String(source.complete_minutes) : "",
      acceptMinutes: source ? String(source.accept_minutes != null ? source.accept_minutes : source.respond_minutes) : "",
      warningLeadMinutes: source ? String(source.warning_lead_minutes != null ? source.warning_lead_minutes
        : Math.min(source.complete_minutes, Math.max(Math.floor(source.complete_minutes / 4), 15))) : "",
      escalationMinutes: source ? String(source.escalation_minutes != null ? source.escalation_minutes : 0) : "",
      changeReason: "",
      statusIndex,
    });
  },

  save() {
    const generation = capturePage(this);
    if (this._hidden || !this.data.policiesReady) {
      this.setData({ error: "请先成功读取当前小区的策略，再保存修改" });
      return;
    }
    if (this.data.loading) {
      return;
    }
    const respondMinutes = Number(this.data.respondMinutes);
    const completeMinutes = Number(this.data.completeMinutes);
    const acceptMinutes = Number(this.data.acceptMinutes);
    const warningLeadMinutes = Number(this.data.warningLeadMinutes);
    const escalationMinutes = Number(this.data.escalationMinutes);
    if (
      !Number.isInteger(respondMinutes) ||
      !Number.isInteger(completeMinutes) ||
      respondMinutes < 1 ||
      completeMinutes < 1 ||
      respondMinutes > completeMinutes ||
      respondMinutes > 2147483647 ||
      completeMinutes > 2147483647
    ) {
      this.setData({ error: "时限必须是正整数分钟，且接单/响应不能晚于完成时限" });
      return;
    }
    if (this.data.versioningAvailable && (
      !Number.isInteger(acceptMinutes) || acceptMinutes < 1 || acceptMinutes > respondMinutes ||
      !Number.isInteger(warningLeadMinutes) || warningLeadMinutes < 0 ||
      !Number.isInteger(escalationMinutes) || escalationMinutes < 0
    )) {
      this.setData({ error: "接单、预警和升级配置无效" });
      return;
    }
    if (this.data.versioningAvailable && this.data.changeReason.trim().length < 2) {
      this.setData({ error: "请填写本次 SLA 策略变更原因" });
      return;
    }
    const scope = optionValue(this.data.scopeOptions, this.data.scopeIndex);
    if (scope === "TENANT" && !this.data.canTenant) {
      this.setData({ error: "当前身份不能修改企业默认策略" });
      return;
    }
    const body = {
      tenant_id: Number(this.data.tenantId),
      property_id: scope === "PROPERTY" ? Number(this.data.propertyId) : null,
      category: optionValue(CATEGORY_OPTIONS, this.data.categoryIndex),
      priority: optionValue(PRIORITY_OPTIONS, this.data.priorityIndex),
      respond_minutes: respondMinutes,
      complete_minutes: completeMinutes,
      accept_minutes: this.data.versioningAvailable ? acceptMinutes : null,
      warning_lead_minutes: this.data.versioningAvailable ? warningLeadMinutes : null,
      escalation_minutes: this.data.versioningAvailable ? escalationMinutes : null,
      change_reason: this.data.versioningAvailable ? this.data.changeReason.trim() : null,
      status: optionValue(this.data.visibleStatusOptions, this.data.statusIndex),
      expected_updated_at: this.data.expectedUpdatedAt || null,
    };
    withMutation(this, () =>
      api.putSlaPolicy(body).then((saved) => {
        if (!isPageCurrent(this, generation)) return;
        this.setData({
          notice: `策略 #${saved.id} 已保存；刷新后以服务端结果为准`,
        });
        return this.fetchPolicies();
      })
    ).catch(() => {});
  },
});

module.exports = {
  scopeAllows,
  tenantScopeAllows,
  sourceLabel,
};
