function scopeAllows(scope, permission, property) {
  if ((scope.permissions || []).indexOf(permission) < 0) return false;
  if (scope.scope_type === "SYSTEM") return true;
  if (Number(scope.tenant_id) !== Number(property.tenant_id)) return false;
  return scope.scope_type === "TENANT" ||
    (scope.scope_type === "PROPERTY" && Number(scope.property_id) === Number(property.id));
}

function loadAllProperties(api, afterId, collected) {
  const current = collected || [];
  return api.catalogProperties(afterId || 0, 100).then((payload) => {
    const items = (payload && payload.items) || [];
    const next = current.concat(items);
    if (items.length < 100) return next;
    const cursor = Number(items[items.length - 1].id);
    if (!Number.isSafeInteger(cursor) || cursor <= Number(afterId || 0)) {
      throw new Error("小区列表加载异常，请刷新后重试");
    }
    return loadAllProperties(api, cursor, next);
  });
}

function authorizedProperties(api, permission) {
  return Promise.all([api.me(), loadAllProperties(api)]).then(([me, properties]) => {
    const scopes = (me && me.scopes) || [];
    return properties.filter((property) =>
      scopes.some((scope) => scopeAllows(scope, permission, property))
    );
  });
}

function preferredIndex(properties, requestedId) {
  const index = (properties || []).findIndex(
    (property) => Number(property.id) === Number(requestedId)
  );
  return index >= 0 ? index : 0;
}

module.exports = { authorizedProperties, preferredIndex, scopeAllows };
