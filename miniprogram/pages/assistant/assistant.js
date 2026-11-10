const api = require("../../services/api");

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

Page({
  data: {
    properties: [],
    propertyIndex: 0,
    propertyId: "",
    question: "",
    answer: "",
    answerStatus: "",
    citations: [],
    error: "",
    loading: false,
    audience: "resident",
    audienceIndex: 0,
    audienceOptions: ["住户知识", "物业内部知识"],
    canReadInternal: false,
  },

  onLoad(query) {
    this.requestedPropertyId = query.propertyId || "";
  },

  onShow() {
    return this.bootstrap();
  },

  onHide() {
    this.clearAnswer();
  },

  onUnload() {
    this.clearAnswer();
  },

  clearAnswer() {
    this.queryVersion = (this.queryVersion || 0) + 1;
    this.setData({ answer: "", answerStatus: "", citations: [], error: "", loading: false });
    return this.queryVersion;
  },

  internalScopeAllows(property) {
    return (this.knowledgeScopes || []).some((scope) =>
      ["STAFF", "SUPERVISOR", "PROPERTY_ADMIN", "TENANT_ADMIN", "SYSTEM_ADMIN"].includes(scope.role_code) &&
      scopeAllows(scope, "knowledge:read", property)
    );
  },

  bootstrap() {
    const version = this.clearAnswer();
    this.knowledgeScopes = [];
    this.setData({ properties: [], propertyId: "", audience: "resident", audienceIndex: 0, canReadInternal: false, loading: true });
    return Promise.all([api.me(), loadAllProperties()]).then(([me, allProperties]) => {
        if (version !== this.queryVersion) return;
        const scopes = me.scopes || [];
        this.knowledgeScopes = scopes;
        const properties = allProperties.filter((property) =>
          scopes.some((scope) => scopeAllows(scope, "knowledge:read", property))
        );
        if (!properties.length) {
          throw new Error("当前身份没有可查询的知识库范围");
        }
        let propertyIndex = properties.findIndex(
          (item) => Number(item.id) === Number(this.requestedPropertyId)
        );
        if (propertyIndex < 0) {
          propertyIndex = 0;
        }
        this.setData({
          properties,
          propertyIndex,
          propertyId: String(properties[propertyIndex].id),
          canReadInternal: this.internalScopeAllows(properties[propertyIndex]),
          error: "",
        });
      })
      .catch((error) => {
        if (version === this.queryVersion) this.setData({ error: error.message || "加载失败" });
      })
      .finally(() => {
        if (version === this.queryVersion) this.setData({ loading: false });
      });
  },

  onPropertyChange(event) {
    const propertyIndex = Number(event.detail.value);
    const property = this.data.properties[propertyIndex];
    if (!property) {
      return;
    }
    this.clearAnswer();
    this.setData({
      propertyIndex,
      propertyId: String(property.id),
      audience: "resident",
      audienceIndex: 0,
      canReadInternal: this.internalScopeAllows(property),
    });
  },

  onAudienceChange(event) {
    const audienceIndex = Number(event.detail.value);
    if (audienceIndex !== 0 && audienceIndex !== 1) return;
    this.clearAnswer();
    this.setData({ audienceIndex, audience: audienceIndex === 1 ? "property" : "resident" });
  },

  onQuestion(event) {
    this.setData({ question: event.detail.value });
  },

  ask() {
    const version = this.clearAnswer();
    const question = String(this.data.question || "").trim();
    if (!question || !this.data.propertyId) {
      this.setData({ error: "请选择小区并填写问题" });
      return;
    }
    this.setData({ loading: true });
    const body = { question, property_id: Number(this.data.propertyId), audience: this.data.audience };
    return Promise.resolve().then(() => {
          if (version !== this.queryVersion) return;
          return api.queryAssistant(body);
        })
        .then((data) => {
          if (version !== this.queryVersion) return;
          this.setData({
            answer: data.answer || "当前知识库未能生成答案",
            answerStatus: data.status || "",
            citations: data.citations || [],
          });
        })
        .catch((error) => {
          if (version === this.queryVersion) this.setData({ error: error.message || "请求失败" });
        })
        .finally(() => {
          if (version === this.queryVersion) this.setData({ loading: false });
        });
  },
});

module.exports = { scopeAllows };
