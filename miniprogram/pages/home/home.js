Page({
  data: {
    me: null,
    canResident: false,
    canStaff: false,
    canManage: false,
    propertyId: "",
    loading: false,
    error: "",
  },
  onShow() {
    const api = require("../../services/api");
    const { withLoading, firstPropertyId } = require("../../utils/ui");
    withLoading(this, () =>
      api.me().then((me) => {
        const permissions = me.permissions || [];
        getApp().globalData.me = me;
        this.setData({
          me,
          canResident: permissions.indexOf("work_order:create") >= 0,
          canStaff: permissions.indexOf("work_order:process") >= 0,
          canManage: permissions.indexOf("work_order:dispatch") >= 0,
          propertyId: firstPropertyId(me),
        });
      })
    ).catch(() => {});
  },
  retry() {
    this.onShow();
  },
  go(event) {
    const url = event.currentTarget.dataset.url;
    const propertyId = this.data.propertyId;
    const needsProperty =
      url.indexOf("announcements") >= 0 ||
      url.indexOf("assistant") >= 0 ||
      url.indexOf("supervisor") >= 0 ||
      url.indexOf("attendance") >= 0;
    if (needsProperty && propertyId) {
      const join = url.indexOf("?") >= 0 ? "&" : "?";
      wx.navigateTo({ url: `${url}${join}propertyId=${propertyId}` });
      return;
    }
    wx.navigateTo({ url });
  },
});
