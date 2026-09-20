Page({
  data: {
    me: null,
    canResident: false,
    canStaff: false,
    canManage: false,
    canManageStaff: false,
    canManageProperty: false,
    propertyId: "",
    tenantId: "",
    loading: false,
    error: "",
    providerNote: "",
    envNote: "",
    lastWorkOrderId: "",
  },
  onShow() {
    const env = require("../../config/env");
    const auth = require("../../services/auth");
    this.setData({
      envNote: env.ENV_NOTE,
      lastWorkOrderId: auth.getLastWorkOrderId(),
    });
    const api = require("../../services/api");
    const { withLoading, firstPropertyId, firstTenantId } = require("../../utils/ui");
    withLoading(this, () =>
      api.me().then((me) => {
        const permissions = me.permissions || [];
        getApp().globalData.me = me;
        const apply = (propertyId, tenantId) =>
          this.setData({
            me,
            canResident: permissions.indexOf("work_order:create") >= 0,
            canStaff: permissions.indexOf("work_order:process") >= 0,
            canManage: permissions.indexOf("work_order:dispatch") >= 0,
            canManageStaff: permissions.indexOf("employee:read") >= 0,
            canManageProperty: permissions.indexOf("property:manage") >= 0,
            propertyId,
            tenantId,
          });
        const scopedPropertyId = firstPropertyId(me);
        const scopedTenantId = firstTenantId(me);
        if (scopedPropertyId || !scopedTenantId) {
          apply(scopedPropertyId, scopedTenantId);
          return null;
        }
        return api.catalogProperties().then((payload) => {
          const property = ((payload && payload.items) || []).find(
            (item) => Number(item.tenant_id) === Number(scopedTenantId)
          );
          apply(property ? property.id : "", scopedTenantId);
        });
      })
    ).catch(() => {});
  },
  retry() {
    this.onShow();
  },
  switchIdentity() {
    this.logout();
  },
  logout() {
    const auth = require("../../services/auth");
    const api = require("../../services/api");
    const refresh = auth.getRefreshToken();
    const finish = () => {
      auth.clearSession();
      wx.reLaunch({ url: "/pages/login/login" });
    };
    if (!refresh) {
      finish();
      return;
    }
    api.logout(refresh).then(finish).catch(finish);
  },
  openLast() {
    const id = this.data.lastWorkOrderId;
    if (!id) {
      this.setData({ error: "还没有本机记录的工单" });
      return;
    }
    wx.navigateTo({ url: `/pages/work-orders/detail?id=${id}` });
  },
  go(event) {
    const url = event.currentTarget.dataset.url;
    const propertyId = this.data.propertyId;
    const tenantId = this.data.tenantId;
    const needsProperty =
      url.indexOf("announcements") >= 0 ||
      url.indexOf("assistant") >= 0 ||
      url.indexOf("supervisor") >= 0 ||
      url.indexOf("attendance") >= 0;
    if (needsProperty && propertyId) {
      const join = url.indexOf("?") >= 0 ? "&" : "?";
      const tenant = tenantId ? `&tenantId=${tenantId}` : "";
      wx.navigateTo({ url: `${url}${join}propertyId=${propertyId}${tenant}` });
      return;
    }
    wx.navigateTo({ url });
  },
});
