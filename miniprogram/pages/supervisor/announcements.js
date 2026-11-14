const { formatBeijingTime } = require("../../utils/date-time");
const api = require("../../services/api");
const { withLoading, withMutation, capturePage, isPageCurrent, resetPage } = require("../../utils/page-context");

const SCOPE_OPTIONS = [
  { value: "PROPERTY", label: "当前小区" },
  { value: "BUILDING", label: "指定楼栋" },
  { value: "UNIT", label: "指定单元" },
  { value: "AUDIENCE", label: "当前小区指定人群" },
  { value: "TENANT", label: "企业全部小区" },
];

const AUDIENCE_OPTIONS = [
  { value: "RESIDENT", label: "全部住户" },
  { value: "OWNER", label: "业主" },
  { value: "TENANT", label: "租户" },
  { value: "STAFF", label: "员工" },
];

function indexOfValue(items, value) {
  const index = items.findIndex((item) => item.value === value);
  if (index < 0) throw new Error("公告范围或人群已变化，请重新选择");
  return index;
}

function decorate(item) {
  const labels = require("../../utils/business-labels");
  const scope = (item.scopes || [])[0] || {};
  const audience = AUDIENCE_OPTIONS.find((row) => row.value === scope.audience);
  const target = SCOPE_OPTIONS.find((row) => row.value === scope.scope_type);
  return {
    ...item,
    statusText: labels.announcementStatus(item.status),
    reviews: (item.reviews || []).map((review) => ({
      ...review,
      createdAtLabel: formatBeijingTime(review.created_at),
      decisionText: labels.announcementReviewDecision(review.decision),
    })),
    history: (item.history || []).map((history) => ({
      ...history,
      createdAtLabel: formatBeijingTime(history.created_at),
      actionText: labels.announcementHistoryAction(history.action),
      statusText: labels.announcementStatus(history.details && history.details.status),
    })),
    scope,
    targetText: `${target ? target.label : scope.scope_type || "未配置"} · ${
      audience ? audience.label : scope.audience || "未配置"
    }`,
  };
}

function scopeAllows(scope, permission, tenantId, propertyId) {
  if ((scope.permissions || []).indexOf(permission) < 0) {
    return false;
  }
  if (scope.scope_type === "SYSTEM") {
    return true;
  }
  if (Number(scope.tenant_id) !== Number(tenantId)) {
    return false;
  }
  return (
    scope.scope_type === "TENANT" ||
    (scope.scope_type === "PROPERTY" &&
      Number(scope.property_id) === Number(propertyId))
  );
}

function tenantScopeAllows(scope, permission, tenantId) {
  if ((scope.permissions || []).indexOf(permission) < 0) {
    return false;
  }
  if (scope.scope_type === "SYSTEM") {
    return true;
  }
  return (
    scope.scope_type === "TENANT" &&
    Number(scope.tenant_id) === Number(tenantId)
  );
}

function loadAllAnnouncements(propertyId, afterId, collected) {
  const current = collected || [];
  return api.adminAnnouncements(propertyId, afterId || 0).then((response) => {
    const items = (response && response.items) || [];
    const next = current.concat(items);
    const nextCursor = response && response.next_cursor;
    if (!nextCursor) {
      return next;
    }
    if (Number(nextCursor) <= Number(afterId || 0)) {
      throw new Error("公告分页游标无效，请刷新后重试");
    }
    return loadAllAnnouncements(propertyId, nextCursor, next);
  });
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

function loadCatalog(fetch, afterId = 0, collected = []) {
  return fetch(afterId).then((payload) => {
    const rows = (payload && payload.items) || [];
    const next = collected.concat(rows);
    if (!payload.next_cursor && rows.length < 50) return next;
    const cursor = Number(payload.next_cursor || rows[rows.length - 1].id);
    if (!Number.isSafeInteger(cursor) || cursor <= afterId) throw new Error("目录分页异常，请刷新后重试");
    return loadCatalog(fetch, cursor, next);
  });
}

function verifiedProperty(properties, propertyId) {
  const property = (properties || []).find(
    (item) => Number(item.id) === Number(propertyId)
  );
  if (!property) {
    throw new Error("当前身份没有该小区的管理权限");
  }
  return property;
}

Page({
  data: {
    propertyId: "",
    tenantId: "",
    title: "",
    content: "",
    reviewReason: "",
    items: [],
    selected: null,
    selectedId: "",
    loading: false,
    error: "",
    notice: "",
    canDraft: false,
    canPublish: false,
    canPublishProperty: false,
    canPublishTenant: false,
    canTenant: false,
    scopeOptions: SCOPE_OPTIONS,
    scopeIndex: 0,
    audienceOptions: AUDIENCE_OPTIONS,
    audienceIndex: 0,
    buildings: [],
    buildingIndex: 0,
    units: [],
    unitIndex: 0,
  },
  onLoad(query) { this.setData({ propertyId: query.propertyId || "", tenantId: "" }); },
  onShow() {
    if (this._pendingMutation) { this._hidden = false; this.setData({ loading: true }); return Promise.resolve(); }
    resetPage(this, false, {canDraft: false, canPublish: false, canPublishProperty: false, canPublishTenant: false, canTenant: false, buildings: [], units: [], title: "", content: "", reviewReason: ""});
    return this.load();
  },
  onHide() { resetPage(this, true, {canDraft: false, canPublish: false, canPublishProperty: false, canPublishTenant: false, canTenant: false, buildings: [], units: [], title: "", content: "", reviewReason: ""}); },
  onUnload() { this.onHide(); },
  onField(e) {
    if (this._hidden || this.data.loading) return;
    this.setData({ [e.currentTarget.dataset.field]: e.detail.value });
  },
  onScopeChange(e) {
    if (this._hidden || this.data.loading) return;
    this.setData({ scopeIndex: Number(e.detail.value) });
  },
  onAudienceChange(e) {
    if (this._hidden || this.data.loading) return;
    this.setData({ audienceIndex: Number(e.detail.value) });
  },
  onBuildingChange(e) {
    if (this._hidden || this.data.loading) return;
    const buildingIndex = Number(e.detail.value);
    this.setData({ buildingIndex, unitIndex: 0 });
    return withLoading(this, () => this.loadUnits(this.data.buildings[buildingIndex])).catch(() => {});
  },
  onUnitChange(e) {
    if (this._hidden || this.data.loading) return;
    this.setData({ unitIndex: Number(e.detail.value) });
  },
  loadUnits(building) {
    const generation = capturePage(this);
    this.setData({ units: [], unitIndex: -1 });
    if (!building) {
      this.setData({ units: [], unitIndex: -1 });
      return Promise.resolve([]);
    }
    return loadCatalog((after) => api.catalogUnits(building.id, after, 50)).then((units) => {
        if (!isPageCurrent(this, generation)) return;
      this.setData({ units, unitIndex: 0 });
      return units;
    });
  },
  load() {
    const generation = capturePage(this);
    if (!this.data.propertyId) {
      this.setData({ error: "缺少有权管理的小区" });
      return Promise.resolve();
    }
    return withLoading(this, () =>
      Promise.all([api.me(), loadAllProperties()]).then(([me, properties]) => {
        if (!isPageCurrent(this, generation)) return;
        const property = verifiedProperty(properties, this.data.propertyId);
        const scopes = me.scopes || [];
        const tenantId = property.tenant_id;
        const canDraft = scopes.some((scope) =>
          scopeAllows(scope, "announcement:draft", tenantId, this.data.propertyId)
        );
        const canPublishProperty = scopes.some((scope) =>
          scopeAllows(scope, "announcement:publish", tenantId, this.data.propertyId)
        );
        const canTenant = scopes.some((scope) =>
          tenantScopeAllows(scope, "announcement:draft", tenantId)
        );
        const canPublishTenant = scopes.some((scope) =>
          tenantScopeAllows(scope, "announcement:publish", tenantId)
        );
        return Promise.all([
          loadCatalog((after) => api.catalogBuildings(this.data.propertyId, after, 50)),
          loadAllAnnouncements(this.data.propertyId),
        ]).then(([buildings, announcements]) => {
        if (!isPageCurrent(this, generation)) return;
          const items = announcements.map(decorate);
          const selectedIsTenant =
            this.data.selected && this.data.selected.scope.scope_type === "TENANT";
          this.setData({
            tenantId: String(tenantId),
            canDraft,
            canPublish: selectedIsTenant ? canPublishTenant : canPublishProperty,
            canPublishProperty,
            canPublishTenant,
            canTenant,
            buildings,
            items,
            error: "",
          });
          if (buildings.length && !this.data.units.length) {
            return this.loadUnits(buildings[0]);
          }
          return null;
        });
      })
    ).catch(() => {});
  },
  targetBody() {
    const scope = SCOPE_OPTIONS[this.data.scopeIndex];
    const audience = AUDIENCE_OPTIONS[this.data.audienceIndex];
    const building = this.data.buildings[this.data.buildingIndex];
    const unit = this.data.units[this.data.unitIndex];
    if (!scope || !audience) throw new Error("请选择有效的公告范围和人群");
    if (scope.value === "TENANT" && !this.data.canTenant) {
      throw new Error("当前身份只能管理授权小区，不能定向企业全部小区");
    }
    if ((scope.value === "BUILDING" || scope.value === "UNIT") && !building) {
      throw new Error("请选择楼栋");
    }
    if (scope.value === "UNIT" && !unit) {
      throw new Error("请选择单元");
    }
    return {
      property_id: Number(this.data.propertyId),
      title: this.data.title,
      content: this.data.content,
      scope_type: scope.value,
      building_id:
        scope.value === "BUILDING" || scope.value === "UNIT" ? building.id : undefined,
      unit_id: scope.value === "UNIT" ? unit.id : undefined,
      audience: audience.value,
    };
  },
  create() {
    if (this._hidden || this.data.loading) return;
    const generation = capturePage(this);
    let body;
    try {
      body = this.targetBody();
    } catch (error) {
      this.setData({ error: error.message });
      return;
    }
    withMutation(this, () =>
      api.createAnnouncement(body).then((item) => {
        if (!isPageCurrent(this, generation)) return;
        this.setData({ selectedId: item.id, notice: `草稿 ${item.id} 已保存` });
        return this.reloadSelected(item.id);
      })
    ).catch(() => {});
  },
  update() {
    if (this._hidden || this.data.loading) return;
    const generation = capturePage(this);
    if (!this.data.selectedId) {
      this.setData({ error: "请先选择公告" });
      return;
    }
    let body;
    try {
      body = this.targetBody();
      delete body.property_id;
    } catch (error) {
      this.setData({ error: error.message });
      return;
    }
    withMutation(this, () =>
      api.updateAnnouncement(this.data.selectedId, body).then(() => {
        if (!isPageCurrent(this, generation)) return;
        this.setData({ notice: "草稿修改已保存" });
        return this.reloadSelected(this.data.selectedId);
      })
    ).catch(() => {});
  },
  select(e) {
    if (this._hidden || this.data.loading) return;
    this.loadSelected(Number(e.currentTarget.dataset.id));
  },
  loadSelected(id) {
    const generation = capturePage(this);
    this.setData({selected: null, selectedId: "", buildingIndex: -1, unitIndex: -1, units: [], title: "", content: ""});
    return withLoading(this, () => api.adminAnnouncement(id).then(async (raw) => {
      if (!isPageCurrent(this, generation)) return;
      if (Number(raw.tenant_id) !== Number(this.data.tenantId) || (raw.property_id != null && Number(raw.property_id) !== Number(this.data.propertyId))) throw new Error("公告不属于当前范围");
      const item = decorate(raw);
      const scopeIndex = indexOfValue(SCOPE_OPTIONS, item.scope.scope_type);
      const audienceIndex = indexOfValue(AUDIENCE_OPTIONS, item.scope.audience);
      const needsBuilding = ["BUILDING", "UNIT"].includes(item.scope.scope_type);
      const buildingIndex = needsBuilding ? this.data.buildings.findIndex(row => Number(row.id) === Number(item.scope.building_id)) : 0;
      if (needsBuilding && buildingIndex < 0) throw new Error("原公告楼栋已不可用，未更改原定向；请刷新核实");
      const units = await this.loadUnits(this.data.buildings[buildingIndex]);
      if (!isPageCurrent(this, generation)) return;
      const unitIndex = item.scope.scope_type === "UNIT" ? units.findIndex(row => Number(row.id) === Number(item.scope.unit_id)) : 0;
      if (item.scope.scope_type === "UNIT" && unitIndex < 0) throw new Error("原公告单元已不可用，未更改原定向；请刷新核实");
      this.setData({selected:item, selectedId:item.id, title:item.title, content:item.content, reviewReason:"", scopeIndex, audienceIndex, buildingIndex, unitIndex,
        canPublish:item.scope.scope_type === "TENANT" ? this.data.canPublishTenant : this.data.canPublishProperty, error:""});
    })).catch(() => {});
  },
  reloadSelected(id) {
    const generation = capturePage(this);
    return this.load().then(() => { if (isPageCurrent(this, generation)) return this.loadSelected(id); });
  },
  submitReview() {
    if (this._hidden || this.data.loading) return;
    const generation = capturePage(this);
    if (!this.data.selectedId) {
      this.setData({ error: "请先选择公告" });
      return;
    }
    withMutation(this, () =>
      api.submitAnnouncementReview(this.data.selectedId).then(() => {
        if (!isPageCurrent(this, generation)) return;
        this.setData({ notice: "已提交审核，等待有发布权限的管理员决定" });
        return this.reloadSelected(this.data.selectedId);
      })
    ).catch(() => {});
  },
  approve() {
    if (this._hidden || this.data.loading) return;
    this.review("APPROVE");
  },
  reject() {
    if (this._hidden || this.data.loading) return;
    if (!String(this.data.reviewReason || "").trim()) {
      this.setData({ error: "驳回必须填写原因" });
      return;
    }
    this.review("REJECT");
  },
  review(decision) {
    if (this._hidden || this.data.loading) return;
    const generation = capturePage(this);
    if (!this.data.selectedId || !this.data.canPublish) {
      this.setData({ error: "当前身份没有审核发布权限" });
      return;
    }
    withMutation(this, () =>
      api
        .publishAnnouncement(this.data.selectedId, {
          decision,
          reason: this.data.reviewReason || undefined,
        })
        .then(() => {
        if (!isPageCurrent(this, generation)) return;
          this.setData({ notice: decision === "APPROVE" ? "公告已人工审核发布" : "公告已驳回" });
          return this.reloadSelected(this.data.selectedId);
        })
    ).catch(() => {});
  },
});

module.exports = {
  scopeAllows,
  tenantScopeAllows,
  verifiedProperty,
};
