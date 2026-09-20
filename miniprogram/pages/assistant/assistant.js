const api = require("../../services/api");
const { withLoading } = require("../../utils/ui");

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
          error: "",
        });
      })
    ).catch(() => {});
  },

  onPropertyChange(event) {
    const propertyIndex = Number(event.detail.value);
    const property = this.data.properties[propertyIndex];
    if (!property) {
      return;
    }
    this.setData({
      propertyIndex,
      propertyId: String(property.id),
      answer: "",
      answerStatus: "",
      citations: [],
    });
  },

  onQuestion(event) {
    this.setData({ question: event.detail.value });
  },

  ask() {
    const question = String(this.data.question || "").trim();
    if (!question || !this.data.propertyId) {
      this.setData({ error: "请选择小区并填写问题" });
      return;
    }
    withLoading(this, () =>
      api
        .queryAssistant({
          question,
          property_id: Number(this.data.propertyId),
        })
        .then((data) =>
          this.setData({
            answer: data.answer || "当前知识库未能生成答案",
            answerStatus: data.status || "",
            citations: data.citations || [],
          })
        )
    ).catch(() => {});
  },
});

module.exports = { scopeAllows };
