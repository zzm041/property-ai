const api = require("../../services/api");
const { withLoading, withMutation, capturePage, isPageCurrent, resetPage } = require("../../utils/page-context");

const STATUS_OPTIONS = [
  { value: "", label: "全部状态" },
  { value: "OPEN", label: "待处置" },
  { value: "MONITORING", label: "监控中" },
  { value: "RESOLVED", label: "已解决" },
  { value: "CLOSED", label: "已关闭" },
];

const STATUS_LABELS = STATUS_OPTIONS.reduce((result, item) => {
  if (item.value) {
    result[item.value] = item.label;
  }
  return result;
}, {});

const CATEGORY_LABELS = {
  PLUMBING: "给排水",
  ELECTRICAL: "电气",
  HVAC: "暖通空调",
  CLEANING: "保洁",
  SECURITY: "安防",
  OTHER: "其他",
};

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

const formatDate = require("../../utils/date-time").formatBeijingTime;

function decorate(item) {
  return {
    ...item,
    statusText: STATUS_LABELS[item.status] || item.status,
    categoryText: CATEGORY_LABELS[item.category] || item.category,
    firstSeenText: formatDate(item.first_seen_at),
    lastSeenText: formatDate(item.last_seen_at),
    updatedText: formatDate(item.updated_at),
  };
}

function replaceItem(items, selected) {
  return (items || []).map((item) =>
    Number(item.id) === Number(selected.id) ? decorate(selected) : item
  );
}

Page({
  data: {
    properties: [],
    propertyIndex: 0,
    propertyId: "",
    tenantId: "",
    items: [],
    selected: null,
    selectedId: "",
    statusOptions: STATUS_OPTIONS,
    statusIndex: 0,
    nextCursor: "",
    hasMore: false,
    canManage: false,
    canDraftAnnouncement: false,
    lastDraftId: "",
    loading: false,
    error: "",
    notice: "",
    dispositionReason: "",
  },

  onLoad(query) {
    this.requestedPropertyId = query.propertyId || "";
    this.requestedIncidentId = query.incidentId || "";
  },

  onShow() {
    if (this._pendingMutation) { this._hidden = false; this.setData({ loading: true }); return Promise.resolve(); }
    resetPage(this, false, { canManage: false, canDraftAnnouncement: false, lastDraftId: "", dispositionReason: "", hasMore: false, nextCursor: "" });
    return this.bootstrap();
  },
  onHide() { resetPage(this, true, { canManage: false, canDraftAnnouncement: false, lastDraftId: "", dispositionReason: "", hasMore: false, nextCursor: "" }); },
  onUnload() { this.onHide(); },

  bootstrap() {
    const generation = capturePage(this);
    return withLoading(this, () =>
      Promise.all([api.me(), loadAllProperties()]).then(([me, allProperties]) => {
        if (!isPageCurrent(this, generation)) return;
        const scopes = me.scopes || [];
        const properties = allProperties
          .filter((property) =>
            scopes.some((scope) => scopeAllows(scope, "incident:read", property))
          )
          .map((property) => ({
            ...property,
            canManage: scopes.some((scope) =>
              scopeAllows(scope, "incident:manage", property)
            ),
            canDraftAnnouncement: scopes.some((scope) =>
              scopeAllows(scope, "announcement:draft", property)
            ),
          }));
        if (!properties.length) {
          throw new Error("当前身份没有可查看的事件范围");
        }
        let propertyIndex = properties.findIndex(
          (item) => Number(item.id) === Number(this.requestedPropertyId)
        );
        if (propertyIndex < 0) {
          propertyIndex = 0;
        }
        const current = properties[propertyIndex];
        this.initialized = true;
        this.setData({
          properties,
          propertyIndex,
          propertyId: String(current.id),
          tenantId: String(current.tenant_id),
          canManage: current.canManage,
          canDraftAnnouncement: current.canDraftAnnouncement,
        });
        return this.fetchIncidents(false).then(() => {
        if (!isPageCurrent(this, generation)) return;
          if (this.requestedIncidentId) {
            const incidentId = Number(this.requestedIncidentId);
            this.requestedIncidentId = "";
            return this.loadSelected(incidentId);
          }
          return null;
        });
      })
    ).catch(() => {});
  },

  onPropertyChange(event) {
    if (this._hidden || this.data.loading) return;
    const propertyIndex = Number(event.detail.value);
    const current = this.data.properties[propertyIndex];
    if (!current) {
      return;
    }
    this.requestedPropertyId = String(current.id);
    this.setData({
      propertyIndex,
      propertyId: String(current.id),
      tenantId: String(current.tenant_id),
      canManage: current.canManage,
      canDraftAnnouncement: current.canDraftAnnouncement,
      selected: null,
      selectedId: "",
      items: [],
      dispositionReason: "",
      statusIndex: 0,
      nextCursor: "",
      hasMore: false,
      notice: "",
      lastDraftId: "",
    });
    this.refresh();
  },

  onStatusChange(event) {
    if (this._hidden || this.data.loading) return;
    this.setData({
      statusIndex: Number(event.detail.value),
      selected: null,
      selectedId: "",
      nextCursor: "",
      hasMore: false,
      notice: "",
    });
    this.refresh();
  },

  fetchIncidents(append) {
    const generation = capturePage(this);
    const afterId = append ? this.data.nextCursor : "";
    const selectedStatus = STATUS_OPTIONS[this.data.statusIndex];
    return api
      .incidents(
        this.data.propertyId,
        this.data.tenantId,
        afterId,
        selectedStatus ? selectedStatus.value : ""
      )
      .then((response) => {
        if (!isPageCurrent(this, generation)) return;
        const incoming = ((response && response.items) || []).map(decorate);
        const nextCursor = (response && response.next_cursor) || "";
        if (
          append &&
          nextCursor &&
          Number(nextCursor) <= Number(this.data.nextCursor || 0)
        ) {
          throw new Error("事件分页游标无效，请刷新后重试");
        }
        this.setData({
          items: append ? this.data.items.concat(incoming) : incoming,
          nextCursor: nextCursor ? String(nextCursor) : "",
          hasMore: Boolean(nextCursor),
          error: "",
        });
      });
  },

  refresh() {
    if (!this.data.propertyId || this.data.loading) {
      return Promise.resolve();
    }
    return withLoading(this, () =>
      this.fetchIncidents(false).then(() => {
        if (this.data.selectedId) {
          return this.loadSelected(Number(this.data.selectedId), false);
        }
        return null;
      })
    ).catch(() => {});
  },

  loadMore() {
    if (!this.data.hasMore || this.data.loading) {
      return;
    }
    withLoading(this, () => this.fetchIncidents(true)).catch(() => {});
  },

  selectIncident(event) {
    if (this._hidden || this.data.loading) return;
    withLoading(this, () =>
      this.loadSelected(Number(event.currentTarget.dataset.id), false)
    ).catch(() => {});
  },

  loadSelected(id, updateLoading) {
    const generation = capturePage(this);
    const operation = () =>
      api.incident(id).then((incident) => {
        if (!isPageCurrent(this, generation)) return;
        if (Number(incident.property_id) !== Number(this.data.propertyId) || Number(incident.tenant_id) !== Number(this.data.tenantId)) throw new Error("事件与当前小区不一致，请刷新");
        const selected = decorate(incident);
        this.setData({
          selected,
          selectedId: String(selected.id),
          dispositionReason: "",
          items: replaceItem(this.data.items, selected),
          error: "",
        });
      });
    return updateLoading === false ? operation() : withLoading(this, operation);
  },

  summarize() {
    const generation = capturePage(this);
    if (!this.data.canManage || !this.data.selected || this.data.loading) {
      return;
    }
    const incidentId = this.data.selected.id;
    withMutation(this, () =>
      api.summarizeIncident(incidentId).then((incident) => {
        if (!isPageCurrent(this, generation)) return;
        if (Number(incident.property_id) !== Number(this.data.propertyId) || Number(incident.tenant_id) !== Number(this.data.tenantId)) throw new Error("事件与当前小区不一致，请刷新");
        const selected = decorate(incident);
        this.setData({
          selected,
          items: replaceItem(this.data.items, selected),
          notice: "运营摘要已更新，请核对内容后再用于后续处置。",
        });
      })
    ).catch(() => {});
  },

  onDispositionReasonInput(event) {
    if (this._hidden || this.data.loading) return;
    this.setData({ dispositionReason: event.detail.value || "" });
  },

  updateIncidentStatus(event) {
    const generation = capturePage(this);
    if (!this.data.canManage || !this.data.selected || this.data.loading) {
      return;
    }
    const reason = (this.data.dispositionReason || "").trim();
    const status = event.currentTarget.dataset.status;
    if (reason.length < 2) {
      this.setData({ error: "请填写事件处置或重开原因" });
      return;
    }
    const selected = this.data.selected;
    withMutation(this, () =>
      api
        .updateIncidentStatus(selected.id, {
          status,
          reason,
          expected_updated_at: selected.updated_at,
        })
        .then((incident) => {
        if (!isPageCurrent(this, generation)) return;
          const updated = decorate(incident);
          this.setData({
            selected: updated,
            items: replaceItem(this.data.items, updated),
            dispositionReason: "",
            notice: status === "OPEN" ? "事件已重开并记录原因。" : "事件状态已更新并记录原因。",
            error: "",
          });
        })
    ).catch(() => {});
  },

  createAnnouncementDraft() {
    const generation = capturePage(this);
    if (
      !this.data.canDraftAnnouncement ||
      !this.data.selected ||
      this.data.loading
    ) {
      return;
    }
    withMutation(this, () =>
      api
        .createAnnouncement({
          tenant_id: Number(this.data.tenantId),
          property_id: Number(this.data.propertyId),
          incident_id: Number(this.data.selected.id),
          ai_draft: true,
          scope_type: "PROPERTY",
          audience: "RESIDENT",
        })
        .then((announcement) => {
        if (!isPageCurrent(this, generation)) return;
          this.setData({
            lastDraftId: String(announcement.id),
            notice: "公告草稿已建立，仍须人工编辑、审核后发布。",
          });
        })
    ).catch(() => {});
  },

  openAnnouncements() {
    if (this._hidden || this.data.loading) return;
    wx.navigateTo({
      url: `/pages/supervisor/announcements?propertyId=${this.data.propertyId}&tenantId=${this.data.tenantId}`,
    });
  },
});

module.exports = {
  decorate,
  replaceItem,
  scopeAllows,
};
