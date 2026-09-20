const api = require("../../services/api");
const { withLoading } = require("../../utils/ui");

const TYPE_OPTIONS = ["FAQ", "GUIDE", "RULE", "SOP", "NOTICE"];
const VISIBILITY_OPTIONS = ["RESIDENT", "STAFF_ONLY", "PROPERTY", "TENANT"];

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
  return {
    ...item,
    targetText: item.property_id ? "当前小区" : "企业全部小区",
    jobText: latestJob
      ? `v${latestJob.version} ${latestJob.status}${
          latestJob.error_code ? ` · ${latestJob.error_code}` : ""
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
    this.bootstrap();
  },

  bootstrap() {
    return withLoading(this, () =>
      Promise.all([api.me(), loadAllProperties()]).then(([me, allProperties]) => {
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
    const propertyIndex = Number(event.detail.value);
    const property = this.data.properties[propertyIndex];
    if (!property) {
      return;
    }
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
      notice: "",
    });
    this.load();
  },

  onField(event) {
    this.setData({ [event.currentTarget.dataset.field]: event.detail.value });
  },

  onScopeChange(event) {
    this.setData({ scopeIndex: Number(event.detail.value) });
  },

  onTypeChange(event) {
    this.setData({ typeIndex: Number(event.detail.value) });
  },

  onVisibilityChange(event) {
    this.setData({ visibilityIndex: Number(event.detail.value) });
  },

  onIncludeArchived(event) {
    this.setData({ includeArchived: Boolean(event.detail.value) });
    this.load();
  },

  fetchDocuments() {
    return loadAllDocuments(
      this.data.propertyId,
      this.data.includeArchived
    ).then((items) => {
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
    if (!this.data.propertyId) {
      return Promise.resolve();
    }
    return withLoading(this, () => this.fetchDocuments()).catch(() => {});
  },

  create() {
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
      document_type: TYPE_OPTIONS[this.data.typeIndex],
      visibility: VISIBILITY_OPTIONS[this.data.visibilityIndex],
      source_text: this.data.sourceText,
    };
    withLoading(this, () =>
      api.createKnowledgeDocument(body).then((result) => {
        this.setData({
          notice: `文档 #${result.document_id} 已建立，导入任务为 ${result.job_status}`,
          selectedId: String(result.document_id),
          sourceText: "",
        });
        return this.fetchDocuments().then(() => this.loadSelected(result.document_id));
      })
    ).catch(() => {});
  },

  select(event) {
    this.loadSelected(Number(event.currentTarget.dataset.id));
  },

  loadSelected(id) {
    return withLoading(this, () =>
      api.adminKnowledgeDocument(id).then((item) => {
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
    const selected = this.data.selected;
    if (!selected || selected.status === "ARCHIVED") {
      this.setData({ error: "请先选择未归档的知识文档" });
      return;
    }
    withLoading(this, () =>
      api
        .createKnowledgeVersion(selected.id, {
          tenant_id: selected.tenant_id,
          property_id: selected.property_id,
          source_text: this.data.versionSourceText,
        })
        .then((result) => {
          this.setData({
            notice: `v${result.version} 已提交导入，任务为 ${result.job_status}`,
            versionSourceText: "",
          });
          return this.fetchDocuments().then(() => this.loadSelected(selected.id));
        })
    ).catch(() => {});
  },

  archive() {
    const selected = this.data.selected;
    if (!selected || selected.status === "ARCHIVED") {
      return;
    }
    withLoading(this, () =>
      api
        .archiveKnowledgeDocument(selected.id, {
          tenant_id: selected.tenant_id,
          property_id: selected.property_id,
        })
        .then(() => {
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
