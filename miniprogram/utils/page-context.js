// A page epoch ends when it is hidden or starts rechecking its permissions.
function capturePage(page) { return page._pageGeneration || 0; }
function isPageCurrent(page, generation) {
  return !page._hidden && capturePage(page) === generation;
}
function resetPage(page, hidden, fields) {
  page._pageGeneration = capturePage(page) + 1;
  page._hidden = hidden;
  page.setData({ loading: false, error: "", notice: "", properties: [], items: [],
    selected: null, selectedId: "", ...fields });
}
function withLoading(page, work) {
  const generation = capturePage(page);
  if (!isPageCurrent(page, generation)) return Promise.resolve();
  page.setData({ loading: true, error: "" });
  return Promise.resolve().then(() => {
    if (isPageCurrent(page, generation)) return work();
  }).catch((error) => {
    if (isPageCurrent(page, generation)) page.setData({ error: error.message || "请求失败" });
    throw error;
  }).finally(() => {
    if (isPageCurrent(page, generation)) page.setData({ loading: false });
  });
}
function withMutation(page, work) {
  if (page._hidden || page._pendingMutation) return Promise.resolve();
  const generation = capturePage(page);
  page._pendingMutation = true;
  return withLoading(page, work).finally(() => {
    page._pendingMutation = false;
    // A hidden page discards the response. Returning during that write waits
    // for settlement, then rereads scope and server state instead of resending.
    if (!page._hidden && !isPageCurrent(page, generation)) return page.onShow();
  });
}
module.exports = { capturePage, isPageCurrent, resetPage, withLoading, withMutation };
