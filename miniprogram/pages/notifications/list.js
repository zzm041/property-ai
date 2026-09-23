const PAGE_SIZE = 20;
const api = require("../../services/api");
const { withLoading, workOrderOf } = require("../../utils/ui");
const notificationRoute = require("../../utils/notification-route");
const { presentNotification } = require("../../utils/notification-display");

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
    unread: 0,
    nextCursor: "",
    hasMore: false,
    openingId: "",
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
    const mePromise = append
      ? Promise.resolve(this._me || getApp().globalData.me)
      : api.me().then((me) => {
          this._me = me;
          getApp().globalData.me = me;
          return me;
        });
    return Promise.all([api.notifications(afterId, PAGE_SIZE), mePromise]).then(
      ([data]) => {
        const incoming = ((data && data.items) || []).map(presentNotification);
        const nextCursor = data && data.next_cursor;
        if (
          append &&
          incoming.some((item) => Number(item.id) >= Number(afterId || 0))
        ) {
          throw new Error("通知分页结果无效，请刷新后重试");
        }
        if (append && nextCursor && Number(nextCursor) >= Number(afterId || 0)) {
          throw new Error("通知分页游标无效，请刷新后重试");
        }
        this.setData({
          items: append ? mergeById(this.data.items, incoming) : incoming,
          unread: Number((data && data.unread_count) || 0),
          nextCursor: nextCursor ? String(nextCursor) : "",
          hasMore: Boolean(nextCursor),
          error: "",
        });
      }
    );
  },
  refresh() {
    if (this.data.loading) {
      return Promise.resolve();
    }
    return withLoading(this, () => this.fetchPage(false)).catch(() => {});
  },
  loadMore() {
    if (!this.data.hasMore || this.data.loading) {
      return;
    }
    withLoading(this, () => this.fetchPage(true)).catch(() => {});
  },
  retry() {
    this.refresh();
  },
  async open(e) {
    const id = Number(e.currentTarget.dataset.id);
    if (!id || this.data.openingId) {
      return;
    }
    const notification = this.data.items.find((item) => Number(item.id) === id);
    if (!notification) {
      this.setData({ error: "通知不存在，请刷新后重试" });
      return;
    }
    this.setData({ openingId: String(id), error: "" });
    try {
      if (!notification.read_at) {
        const read = await api.readNotification(id);
        this.setData({
          items: this.data.items.map((item) =>
            Number(item.id) === id ? presentNotification(Object.assign({}, item, read)) : item
          ),
          unread: Math.max(0, Number(this.data.unread || 0) - 1),
        });
      }
      const target = await this.resolveTarget(notification);
      if (!target) {
        wx.showToast({ title: "该通知暂无可打开页面", icon: "none" });
        return;
      }
      wx.navigateTo({ url: target });
    } catch (error) {
      this.setData({ error: error.message || "通知打开失败" });
    } finally {
      this.setData({ openingId: "" });
    }
  },
  async resolveTarget(notification) {
    const me = this._me || getApp().globalData.me;
    const direct = notificationRoute.staticTarget(notification, me);
    if (direct) {
      return direct;
    }
    const resource = notificationRoute.resourceIdentity(notification);
    if (!resource || resource.type !== "WORK_ORDER") {
      return null;
    }
    const detail = await api.workOrder(resource.resourceId);
    const workOrder = workOrderOf(detail);
    if (
      !workOrder ||
      Number(workOrder.tenant_id) !== resource.tenantId ||
      Number(workOrder.property_id) !== resource.propertyId
    ) {
      throw new Error("通知与工单范围不一致");
    }
    if (Number(workOrder.reporter_user_id) === Number(me && me.user_id)) {
      return `/pages/work-orders/detail?id=${resource.resourceId}`;
    }
    try {
      const assigned = await api.staffWorkOrders(resource.resourceId - 1, 1);
      if (
        ((assigned && assigned.items) || []).some(
          (item) => Number(item.id) === resource.resourceId
        )
      ) {
        return `/pages/staff-orders/detail?id=${resource.resourceId}`;
      }
    } catch (_error) {
      // A manager without an Employee row is expected to receive 403 here.
    }
    return notificationRoute.managerTarget(notification, me);
  },
});
