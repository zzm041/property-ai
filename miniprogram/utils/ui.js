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

module.exports = {
  withLoading,
  firstPropertyId,
};
