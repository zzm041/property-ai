Page({
  data: {
    me: null,
    canResident: false,
    canStaff: false,
    canManage: false,
    error: "",
  },
  onShow() {
    const api = require("../../services/api");
    api
      .me()
      .then((me) => {
        const permissions = me.permissions || [];
        this.setData({
          me,
          canResident: permissions.indexOf("work_order:create") >= 0,
          canStaff: permissions.indexOf("work_order:process") >= 0,
          canManage: permissions.indexOf("work_order:dispatch") >= 0,
        });
      })
      .catch((error) => this.setData({ error: error.message }));
  },
  go(event) {
    wx.navigateTo({ url: event.currentTarget.dataset.url });
  },
});
