function withLoading(page, work) {
  page.setData({ loading: true, error: "" });
  return Promise.resolve()
    .then(work)
    .catch((error) => {
      page.setData({ error: error.message || "请求失败" });
      throw error;
    })
    .finally(() => page.setData({ loading: false }));
}

function firstPropertyId(me) {
  const scopes = (me && me.scopes) || [];
  const scoped = scopes.find((item) => item.property_id);
  return scoped ? scoped.property_id : "";
}

function firstTenantId(me) {
  const scopes = (me && me.scopes) || [];
  const scoped = scopes.find((item) => item.tenant_id);
  return scoped ? scoped.tenant_id : "";
}

function workOrderOf(item) {
  if (!item) {
    return null;
  }
  return item.work_order || item;
}

function workOrderStatus(item) {
  const row = workOrderOf(item);
  return row ? row.status : "";
}

module.exports = {
  withLoading,
  firstPropertyId,
  firstTenantId,
  workOrderOf,
  workOrderStatus,
};
