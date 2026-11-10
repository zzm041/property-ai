const { formatBeijingTime } = require("../../utils/date-time");
const api = require("../../services/api");
const { withLoading, withMutation, capturePage, isPageCurrent, resetPage } = require("../../utils/page-context");

const labels = require("../../utils/business-labels");
const TYPE_OPTIONS = ["FAQ", "GUIDE", "RULE", "SOP", "NOTICE"].map((value) => ({
  value,
  label: labels.knowledgeType(value),
}));
const VISIBILITY_OPTIONS = ["RESIDENT", "STAFF_ONLY", "PROPERTY", "TENANT"].map((value) => ({
  value,
  label: labels.knowledgeVisibility(value),
}));

function scopeAllows(scope, permission, property) {
  if ((scope.permissions || []).indexOf(permission) < 0) {
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

function tenantScopeAllows(scope, permission, tenantId) {
  if ((scope.permissions || []).indexOf(permission) < 0) {
    return false;
  }
  return (
    scope.scope_type === "SYSTEM" ||
    (scope.scope_type === "TENANT" &&
      Number(scope.tenant_id) === Number(tenantId))
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

function loadAllDocuments(propertyId, includeArchived, afterId, collected) {
  const current = collected || [];
  return api
    .adminKnowledgeDocuments(propertyId, afterId || 0, includeArchived)
    .then((response) => {
      const items = (response && response.items) || [];
      const next = current.concat(items);
      const nextCursor = response && response.next_cursor;
      if (!nextCursor) {
        return next;
      }
      if (Number(nextCursor) <= Number(afterId || 0)) {
        throw new Error("知识文档分页游标无效，请刷新后重试");
      }
      return loadAllDocuments(propertyId, includeArchived, nextCursor, next);
    });
}

function decorate(item) {
  const jobs = item.ingestion_jobs || [];
  const latestJob = jobs[0];
  const statusLabel = labels.knowledgeStatus;
  return {
    ...item,
    statusText: statusLabel(item.status),
    typeText: labels.knowledgeType(item.document_type),
    visibilityText: labels.knowledgeVisibility(item.visibility),
    versions: (item.versions || []).map((version) => ({
      ...version,
      createdAtLabel: formatBeijingTime(version.created_at),
      statusText: statusLabel(version.status),
    })),
    ingestion_jobs: jobs.map((job) => ({
      ...job,
      statusText: statusLabel(job.status),
    })),
    targetText: item.property_id ? "当前小区" : "企业全部小区",
    jobText: latestJob
      ? `v${latestJob.version} ${statusLabel(latestJob.status)}${
          latestJob.error_code ? " · 请检查正文后重试" : ""
        }`
      : "暂无导入任务",
  };
}

Page({
  data: {
    properties: [],
    propertyIndex: 0,
    propertyId: "",
    tenantId: "",
    canTenant: false,
    scopeOptions: [{ value: "PROPERTY", label: "当前小区" }],
    scopeIndex: 0,
    typeOptions: TYPE_OPTIONS,
    typeIndex: 0,
    visibilityOptions: VISIBILITY_OPTIONS,
    visibilityIndex: 0,
    title: "",
    sourceText: "",
    versionSourceText: "",
    includeArchived: false,
    items: [],
    selected: null,
    selectedId: "",
    loading: false,
    error: "",
    notice: "",
  },

  onLoad(query) {
    this.requestedPropertyId = query.propertyId || "";
  },

  onShow() {
    if (this._pendingMutation) { this._hidden = false; this.setData({ loading: true }); return Promise.resolve(); }
    resetPage(this, false, { canTenant: false, title: "", sourceText: "", versionSourceText: "" });
    return this.bootstrap();
  },
  onHide() { resetPage(this, true, { canTenant: false, title: "", sourceText: "", versionSourceText: "" }); },
  onUnload() { this.onHide(); },

  bootstrap() {
    const generation = capturePage(this);
    return withLoading(this, () =>
      Promise.all([api.me(), loadAllProperties()]).then(([me, allProperties]) => {
        if (!isPageCurrent(this, generation)) return;
        const scopes = me.scopes || [];
        const properties = allProperties.filter((property) =>
          scopes.some((scope) => scopeAllows(scope, "knowledge:manage", property))
        );
        if (!properties.length) {
          throw new Error("当前身份没有可维护知识库的小区范围");
        }
        let propertyIndex = properties.findIndex(
          (item) => Number(item.id) === Number(this.requestedPropertyId)
        );
        if (propertyIndex < 0) {
          propertyIndex = 0;
        }
        const property = properties[propertyIndex];
        const canTenant = scopes.some((scope) =>
          tenantScopeAllows(scope, "knowledge:manage", property.tenant_id)
        );
        this.meScopes = scopes;
        this.setData({
          properties,
          propertyIndex,
          propertyId: String(property.id),
          tenantId: String(property.tenant_id),
          canTenant,
          scopeOptions: [
            { value: "PROPERTY", label: "当前小区" },
            ...(canTenant
              ? [{ value: "TENANT", label: "企业全部小区" }]
              : []),
          ],
          scopeIndex: 0,
        });
        return this.fetchDocuments();
      })
    ).catch(() => {});
  },

  onPropertyChange(event) {
    if (this._hidden || this.data.loading) return;
    const propertyIndex = Number(event.detail.value);
    const property = this.data.properties[propertyIndex];
    if (!property) {
      return;
    }
    this.requestedPropertyId = String(property.id);
    const canTenant = (this.meScopes || []).some((scope) =>
      tenantScopeAllows(scope, "knowledge:manage", property.tenant_id)
    );
    this.setData({
      propertyIndex,
      propertyId: String(property.id),
      tenantId: String(property.tenant_id),
      canTenant,
      scopeOptions: [
        { value: "PROPERTY", label: "当前小区" },
        ...(canTenant ? [{ value: "TENANT", label: "企业全部小区" }] : []),
      ],
      scopeIndex: 0,
      selected: null,
      selectedId: "",
      items: [],
      title: "", sourceText: "", versionSourceText: "",
      notice: "",
    });
    this.load();
  },

  onField(event) {
    if (this._hidden || this.data.loading) return;
    this.setData({ [event.currentTarget.dataset.field]: event.detail.value });
  },

  onScopeChange(event) {
    if (this._hidden || this.data.loading) return;
    this.setData({ scopeIndex: Number(event.detail.value) });
  },

  onTypeChange(event) {
    if (this._hidden || this.data.loading) return;
    this.setData({ typeIndex: Number(event.detail.value) });
  },

  onVisibilityChange(event) {
    if (this._hidden || this.data.loading) return;
    this.setData({ visibilityIndex: Number(event.detail.value) });
  },

  onIncludeArchived(event) {
    if (this._hidden || this.data.loading) return;
    this.setData({ includeArchived: Boolean(event.detail.value) });
    this.load();
  },

  fetchDocuments() {
    const generation = capturePage(this);
    return loadAllDocuments(
      this.data.propertyId,
      this.data.includeArchived
    ).then((items) => {
        if (!isPageCurrent(this, generation)) return;
      const decorated = items.map(decorate);
      const selected = decorated.find(
        (item) => Number(item.id) === Number(this.data.selectedId)
      );
      this.setData({
        items: decorated,
        selected: selected || null,
        selectedId: selected ? String(selected.id) : "",
        error: "",
      });
    });
  },

  load() {
    if (this._hidden || !this.data.propertyId || this.data.loading) {
      return Promise.resolve();
    }
    return withLoading(this, () => this.fetchDocuments()).catch(() => {});
  },

  create() {
    if (this._hidden || this.data.loading) return;
    const generation = capturePage(this);
    const scope = this.data.scopeOptions[this.data.scopeIndex];
    if (!scope) {
      this.setData({ error: "请选择知识文档范围" });
      return;
    }
    if (scope.value === "TENANT" && !this.data.canTenant) {
      this.setData({ error: "当前身份不能维护企业级知识" });
      return;
    }
    const body = {
      tenant_id: Number(this.data.tenantId),
      property_id:
        scope.value === "PROPERTY" ? Number(this.data.propertyId) : null,
      title: this.data.title,
      document_type: TYPE_OPTIONS[this.data.typeIndex].value,
      visibility: VISIBILITY_OPTIONS[this.data.visibilityIndex].value,
      source_text: this.data.sourceText,
    };
    withMutation(this, () =>
      api.createKnowledgeDocument(body).then((result) => {
        if (!isPageCurrent(this, generation)) return;
        this.setData({
          notice: `文档 #${result.document_id} 已建立，导入任务为 ${result.job_status}`,
          selectedId: String(result.document_id),
          sourceText: "",
        });
        return this.fetchDocuments().then(() => { if (isPageCurrent(this, generation)) return this.loadSelected(result.document_id); });
      })
    ).catch(() => {});
  },

  select(event) {
    if (this._hidden || this.data.loading) return;
    this.loadSelected(Number(event.currentTarget.dataset.id));
  },

  loadSelected(id) {
    const generation = capturePage(this);
    return withLoading(this, () =>
      api.adminKnowledgeDocument(id).then((item) => {
        if (!isPageCurrent(this, generation)) return;
        this.setData({
          selected: decorate(item),
          selectedId: String(item.id),
          versionSourceText: "",
          error: "",
        });
      })
    ).catch(() => {});
  },

  createVersion() {
    if (this._hidden || this.data.loading) return;
    const generation = capturePage(this);
    const selected = this.data.selected;
    if (!selected) {
      this.setData({ error: "请先选择知识文档" });
      return;
    }
    withMutation(this, () =>
      api
        .createKnowledgeVersion(selected.id, {
          tenant_id: selected.tenant_id,
          property_id: selected.property_id,
          source_text: this.data.versionSourceText,
        })
        .then((result) => {
        if (!isPageCurrent(this, generation)) return;
          this.setData({
            notice: `v${result.version} 已提交导入，任务为 ${result.job_status}`,
            versionSourceText: "",
          });
          return this.fetchDocuments().then(() => { if (isPageCurrent(this, generation)) return this.loadSelected(selected.id); });
        })
    ).catch(() => {});
  },

  archive() {
    if (this._hidden || this.data.loading) return;
    const generation = capturePage(this);
    const selected = this.data.selected;
    if (!selected || selected.status === "ARCHIVED") {
      return;
    }
    withMutation(this, () =>
      api
        .archiveKnowledgeDocument(selected.id, {
          tenant_id: selected.tenant_id,
          property_id: selected.property_id,
        })
        .then(() => {
        if (!isPageCurrent(this, generation)) return;
          this.setData({ notice: `文档 #${selected.id} 已归档` });
          return this.fetchDocuments();
        })
    ).catch(() => {});
  },
});

module.exports = {
  scopeAllows,
  tenantScopeAllows,
};
