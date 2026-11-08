const PAGE_SIZE = 20;

function mergeById(existing, incoming) {
  const seen = {};
  return existing.concat(incoming).filter((item) => {
    const key = String(item.id);
    if (seen[key]) {
      return false;
    }
    seen[key] = true;
    return true;
  });
}

Page({
  data: {
    items: [],
    nextCursor: "",
    hasMore: false,
    loading: false,
    error: "",
  },
  onShow() {
    this.refresh();
  },
  onPullDownRefresh() {
    this.refresh().finally(() => wx.stopPullDownRefresh());
  },
  onReachBottom() {
    this.loadMore();
  },
  fetchPage(append) {
    const afterId = append ? this.data.nextCursor : "";
    return require("../../services/api")
      .listWorkOrders(afterId, PAGE_SIZE)
      .then((data) => {
        const display = require("../../utils/work-order-display");
        const incoming = ((data && data.items) || []).map(item => ({
          ...item, statusLabel: display.statusLabel(item.status), createdAtLabel: display.utcLabel(item.created_at),
        }));
        const nextCursor = data && data.next_cursor;
        if (
          append &&
          incoming.some((item) => Number(item.id) <= Number(afterId || 0))
        ) {
          throw new Error("工单分页结果无效，请刷新后重试");
        }
        if (append && nextCursor && Number(nextCursor) <= Number(afterId || 0)) {
          throw new Error("工单分页游标无效，请刷新后重试");
        }
        this.setData({
          items: append ? mergeById(this.data.items, incoming) : incoming,
          nextCursor: nextCursor ? String(nextCursor) : "",
          hasMore: Boolean(nextCursor),
          error: "",
        });
      });
  },
  refresh() {
    if (this.data.loading) {
      return Promise.resolve();
    }
    const { withLoading } = require("../../utils/ui");
    return withLoading(this, () => this.fetchPage(false)).catch(() => {});
  },
  loadMore() {
    if (!this.data.hasMore || this.data.loading) {
      return;
    }
    const { withLoading } = require("../../utils/ui");
    withLoading(this, () => this.fetchPage(true)).catch(() => {});
  },
  retry() {
    this.refresh();
  },
  open(e) {
    wx.navigateTo({ url: `/pages/work-orders/detail?id=${e.currentTarget.dataset.id}` });
  },
});
