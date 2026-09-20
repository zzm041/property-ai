const api = require("../../services/api");
const { withLoading } = require("../../utils/ui");

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
  return index < 0 ? 0 : index;
}

function decorate(item) {
  const scope = (item.scopes || [])[0] || {};
  const audience = AUDIENCE_OPTIONS.find((row) => row.value === scope.audience);
  const target = SCOPE_OPTIONS.find((row) => row.value === scope.scope_type);
  return {
    ...item,
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
  onLoad(query) {
    this.setData({
      propertyId: query.propertyId || "",
      tenantId: query.tenantId || "",
    });
    this.load();
  },
  onField(e) {
    this.setData({ [e.currentTarget.dataset.field]: e.detail.value });
  },
  onScopeChange(e) {
    this.setData({ scopeIndex: Number(e.detail.value) });
  },
  onAudienceChange(e) {
    this.setData({ audienceIndex: Number(e.detail.value) });
  },
  onBuildingChange(e) {
    const buildingIndex = Number(e.detail.value);
    this.setData({ buildingIndex, unitIndex: 0 });
    this.loadUnits(this.data.buildings[buildingIndex]);
  },
  onUnitChange(e) {
    this.setData({ unitIndex: Number(e.detail.value) });
  },
  loadUnits(building) {
    if (!building) {
      this.setData({ units: [], unitIndex: 0 });
      return Promise.resolve([]);
    }
    return api.catalogUnits(building.id).then((payload) => {
      const units = (payload && payload.items) || [];
      this.setData({ units, unitIndex: 0 });
      return units;
    });
  },
  load() {
    if (!this.data.propertyId) {
      this.setData({ error: "缺少有权管理的小区" });
      return Promise.resolve();
    }
    return withLoading(this, () =>
      Promise.all([
        api.me(),
        api.catalogBuildings(this.data.propertyId),
        loadAllAnnouncements(this.data.propertyId),
      ]).then(([me, buildingPayload, announcements]) => {
        const scopes = me.scopes || [];
        const propertyGrant = scopes.find(
          (scope) =>
            scope.scope_type === "PROPERTY" &&
            Number(scope.property_id) === Number(this.data.propertyId)
        );
        const tenantId = this.data.tenantId || (propertyGrant && propertyGrant.tenant_id) || "";
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
        const buildings = (buildingPayload && buildingPayload.items) || [];
        const items = announcements.map(decorate);
        const selectedIsTenant =
          this.data.selected && this.data.selected.scope.scope_type === "TENANT";
        this.setData({
          tenantId: String(tenantId || ""),
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
      })
    ).catch(() => {});
  },
  targetBody() {
    const scope = SCOPE_OPTIONS[this.data.scopeIndex];
    const audience = AUDIENCE_OPTIONS[this.data.audienceIndex];
    const building = this.data.buildings[this.data.buildingIndex];
    const unit = this.data.units[this.data.unitIndex];
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
    let body;
    try {
      body = this.targetBody();
    } catch (error) {
      this.setData({ error: error.message });
      return;
    }
    withLoading(this, () =>
      api.createAnnouncement(body).then((item) => {
        this.setData({ selectedId: item.id, notice: `草稿 ${item.id} 已保存` });
        return this.reloadSelected(item.id);
      })
    ).catch(() => {});
  },
  update() {
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
    withLoading(this, () =>
      api.updateAnnouncement(this.data.selectedId, body).then(() => {
        this.setData({ notice: "草稿修改已保存" });
        return this.reloadSelected(this.data.selectedId);
      })
    ).catch(() => {});
  },
  select(e) {
    this.loadSelected(Number(e.currentTarget.dataset.id));
  },
  loadSelected(id) {
    return withLoading(this, () =>
      api.adminAnnouncement(id).then((raw) => {
        const item = decorate(raw);
        const scopeIndex = indexOfValue(SCOPE_OPTIONS, item.scope.scope_type);
        const audienceIndex = indexOfValue(AUDIENCE_OPTIONS, item.scope.audience);
        const buildingIndex = Math.max(
          0,
          this.data.buildings.findIndex(
            (row) => Number(row.id) === Number(item.scope.building_id)
          )
        );
        const selectedBuilding = this.data.buildings[buildingIndex];
        this.setData({
          selected: item,
          selectedId: item.id,
          title: item.title,
          content: item.content,
          reviewReason: "",
          scopeIndex,
          audienceIndex,
          buildingIndex,
          canPublish:
            item.scope.scope_type === "TENANT"
              ? this.data.canPublishTenant
              : this.data.canPublishProperty,
          error: "",
        });
        return this.loadUnits(selectedBuilding).then((units) => {
          const unitIndex = Math.max(
            0,
            units.findIndex((row) => Number(row.id) === Number(item.scope.unit_id))
          );
          this.setData({ unitIndex });
        });
      })
    ).catch(() => {});
  },
  reloadSelected(id) {
    return this.load().then(() => this.loadSelected(id));
  },
  submitReview() {
    if (!this.data.selectedId) {
      this.setData({ error: "请先选择公告" });
      return;
    }
    withLoading(this, () =>
      api.submitAnnouncementReview(this.data.selectedId).then(() => {
        this.setData({ notice: "已提交审核，等待有发布权限的管理员决定" });
        return this.reloadSelected(this.data.selectedId);
      })
    ).catch(() => {});
  },
  approve() {
    this.review("APPROVE");
  },
  reject() {
    if (!String(this.data.reviewReason || "").trim()) {
      this.setData({ error: "驳回必须填写原因" });
      return;
    }
    this.review("REJECT");
  },
  review(decision) {
    if (!this.data.selectedId || !this.data.canPublish) {
      this.setData({ error: "当前身份没有审核发布权限" });
      return;
    }
    withLoading(this, () =>
      api
        .publishAnnouncement(this.data.selectedId, {
          decision,
          reason: this.data.reviewReason || undefined,
        })
        .then(() => {
          this.setData({ notice: decision === "APPROVE" ? "公告已人工审核发布" : "公告已驳回" });
          return this.reloadSelected(this.data.selectedId);
        })
    ).catch(() => {});
  },
});
