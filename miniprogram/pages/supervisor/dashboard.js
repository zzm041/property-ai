const api = require("../../services/api");
const { withLoading } = require("../../utils/ui");

const LABELS = {
  PENDING_ANALYSIS: "待分析",
  PENDING_ASSIGNMENT: "待派单",
  ASSIGNED: "已派单",
  ACCEPTED: "已接单",
  PROCESSING: "处理中",
  WAIT_CONFIRM: "待住户确认",
  COMPLETED: "已完成",
  CANCELLED: "已取消",
  CLOSED: "已关闭",
  PLUMBING: "给排水",
  ELECTRICAL: "电气",
  HVAC: "暖通",
  CLEANING: "保洁",
  SECURITY: "安防",
  OTHER: "其他",
  LOW: "低",
  MEDIUM: "中",
  HIGH: "高",
  URGENT: "紧急",
  OPEN: "开放",
  MONITORING: "观察中",
  RESOLVED: "已解决",
  WARNING: "预警",
  OVERDUE: "逾期",
  STOPPED: "已停止",
};
const SLA_LABELS = { OPEN: "计时中" };

function scopeAllows(scope, permission, tenantId, propertyId) {
  if ((scope.permissions || []).indexOf(permission) < 0) {
    return false;
  }
  if (scope.scope_type === "TENANT") {
    return Number(scope.tenant_id) === Number(tenantId);
  }
  return (
    scope.scope_type === "PROPERTY" &&
    Number(scope.tenant_id) === Number(tenantId) &&
    Number(scope.property_id) === Number(propertyId)
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

function labelled(items, overrides) {
  return (items || []).map((item) => ({
    ...item,
    label: (overrides && overrides[item.code]) || LABELS[item.code] || item.code,
  }));
}

function overviewMatches(overview, scopeType, tenantId, propertyId) {
  if (
    !overview ||
    overview.scope_type !== scopeType ||
    Number(overview.tenant_id) !== Number(tenantId)
  ) {
    return false;
  }
  if (scopeType === "TENANT") {
    return overview.property_id === null;
  }
  return Number(overview.property_id) === Number(propertyId);
}

Page({
  data: {
    tenants: [],
    tenantIndex: 0,
    tenantId: "",
    properties: [],
    propertyIndex: 0,
    propertyId: "",
    scopeOptions: [],
    scopeIndex: 0,
    overview: null,
    workOrderStatuses: [],
    workOrderCategories: [],
    workOrderPriorities: [],
    incidentStatuses: [],
    slaStates: [],
    configEntries: [],
    loading: false,
    error: "",
  },

  onLoad(query) {
    this.requestedTenantId = query.tenantId || "";
    this.requestedPropertyId = query.propertyId || "";
  },

  onShow() {
    this.bootstrap();
  },

  bootstrap() {
    return withLoading(this, () =>
      Promise.all([api.me(), loadAllProperties()]).then(([me, allProperties]) => {
        this.scopes = me.scopes || [];
        const statsScopes = this.scopes.filter(
          (scope) => (scope.permissions || []).indexOf("stats:read") >= 0
        );
        const tenantIds = [];
        statsScopes.forEach((scope) => {
          const tenantId = Number(scope.tenant_id);
          if (tenantId > 0 && tenantIds.indexOf(tenantId) < 0) {
            tenantIds.push(tenantId);
          }
        });
        const authorizedProperties = allProperties.filter((property) =>
          statsScopes.some((scope) =>
            scopeAllows(scope, "stats:read", property.tenant_id, property.id)
          )
        );
        if (!tenantIds.length) {
          throw new Error("当前身份没有运营统计权限");
        }
        this.allProperties = authorizedProperties;
        const tenants = tenantIds.sort((left, right) => left - right).map((id) => ({
          id,
          name: `企业 #${id}`,
        }));
        let tenantIndex = tenants.findIndex(
          (item) => Number(item.id) === Number(this.requestedTenantId)
        );
        if (tenantIndex < 0 && this.requestedPropertyId) {
          const requested = authorizedProperties.find(
            (item) => Number(item.id) === Number(this.requestedPropertyId)
          );
          tenantIndex = requested
            ? tenants.findIndex((item) => Number(item.id) === Number(requested.tenant_id))
            : -1;
        }
        this.setData({ tenants, tenantIndex: Math.max(tenantIndex, 0) });
        this.applyTenant(Math.max(tenantIndex, 0));
        return this.fetchOverview();
      })
    ).catch(() => {});
  },

  applyTenant(tenantIndex) {
    const tenant = this.data.tenants[tenantIndex];
    const tenantId = tenant ? Number(tenant.id) : 0;
    const properties = (this.allProperties || []).filter(
      (item) => Number(item.tenant_id) === tenantId
    );
    let propertyIndex = properties.findIndex(
      (item) => Number(item.id) === Number(this.requestedPropertyId)
    );
    if (propertyIndex < 0) {
      propertyIndex = 0;
    }
    const property = properties[propertyIndex];
    const canTenant = (this.scopes || []).some(
      (scope) =>
        scope.scope_type === "TENANT" &&
        Number(scope.tenant_id) === tenantId &&
        (scope.permissions || []).indexOf("stats:read") >= 0
    );
    const scopeOptions = [
      ...(property ? [{ value: "PROPERTY", label: "当前小区" }] : []),
      ...(canTenant ? [{ value: "TENANT", label: "企业汇总" }] : []),
    ];
    this.setData({
      tenantIndex,
      tenantId: String(tenantId),
      properties,
      propertyIndex,
      propertyId: property ? String(property.id) : "",
      scopeOptions,
      scopeIndex: 0,
      overview: null,
      configEntries: this.configEntries(tenantId, property && property.id),
      error: "",
    });
  },

  configEntries(tenantId, propertyId) {
    if (!propertyId) {
      return [];
    }
    const candidates = [
      { permission: "employee:manage", label: "员工职责、技能与容量", path: "/pages/supervisor/staff" },
      { permission: "sla:manage", label: "SLA 策略", path: "/pages/supervisor/sla" },
      { permission: "property:manage", label: "楼栋房屋与住户关系", path: "/pages/supervisor/properties" },
      { permission: "knowledge:manage", label: "知识库维护", path: "/pages/supervisor/knowledge" },
    ];
    return candidates.filter((entry) =>
      (this.scopes || []).some((scope) =>
        scopeAllows(scope, entry.permission, tenantId, propertyId)
      )
    );
  },

  onTenantChange(event) {
    this.requestedPropertyId = "";
    this.applyTenant(Number(event.detail.value));
    this.fetchOverview().catch(() => {});
  },

  onPropertyChange(event) {
    const propertyIndex = Number(event.detail.value);
    const property = this.data.properties[propertyIndex];
    if (!property) {
      return;
    }
    const propertyScoped = this.currentScope() === "PROPERTY";
    this.setData({
      propertyIndex,
      propertyId: String(property.id),
      configEntries: this.configEntries(property.tenant_id, property.id),
      overview: propertyScoped ? null : this.data.overview,
    });
    if (propertyScoped) {
      this.fetchOverview().catch(() => {});
    }
  },

  onScopeChange(event) {
    this.setData({ scopeIndex: Number(event.detail.value), overview: null });
    this.fetchOverview().catch(() => {});
  },

  currentScope() {
    const selected = this.data.scopeOptions[this.data.scopeIndex];
    return selected ? selected.value : "";
  },

  fetchOverview() {
    const scopeType = this.currentScope();
    if (!scopeType || !this.data.tenantId) {
      return Promise.reject(new Error("没有可读取的统计范围"));
    }
    const propertyId = scopeType === "PROPERTY" ? this.data.propertyId : "";
    const tenantId = this.data.tenantId;
    const sequence = Number(this.overviewSequence || 0) + 1;
    this.overviewSequence = sequence;
    this.setData({ loading: true, error: "" });
    return api
      .operationsOverview(scopeType, tenantId, propertyId)
      .then((overview) => {
        if (sequence !== this.overviewSequence) {
          return;
        }
        if (!overviewMatches(overview, scopeType, tenantId, propertyId)) {
          throw new Error("统计响应范围与当前选择不一致，请刷新重试");
        }
        this.setData({
          overview: { ...overview, generatedAtLabel: require("../../utils/date-time").formatBeijingTime(overview.generated_at) },
          workOrderStatuses: labelled(overview.work_orders.by_status),
          workOrderCategories: labelled(overview.work_orders.by_category),
          workOrderPriorities: labelled(overview.work_orders.by_priority),
          incidentStatuses: labelled(overview.incidents.by_status),
          slaStates: labelled(overview.sla_clocks.by_state, SLA_LABELS),
          error: "",
        });
      })
      .catch((error) => {
        if (sequence === this.overviewSequence) {
          this.setData({ error: error.message || "统计读取失败" });
        }
        throw error;
      })
      .finally(() => {
        if (sequence === this.overviewSequence) {
          this.setData({ loading: false });
        }
      });
  },

  refresh() {
    this.fetchOverview().catch(() => {});
  },

  openConfig(event) {
    const path = event.currentTarget.dataset.path;
    if (!path || !this.data.propertyId) {
      return;
    }
    wx.navigateTo({
      url: `${path}?propertyId=${this.data.propertyId}&tenantId=${this.data.tenantId}`,
    });
  },
});

module.exports = { scopeAllows, labelled, overviewMatches };
