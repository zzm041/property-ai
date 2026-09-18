const env = require("../../config/env");

Page({
  data: {
    items: [],
    requests: [],
    houseId: "",
    loading: false,
    error: "",
  },
  onShow() {
    const auth = require("../../services/auth");
    this.setData({
      houseId: auth.getSelectedHouseId() || env.PREVIEW_HOUSE_ID || "",
    });
    this.reload();
  },
  onHouse(e) {
    this.setData({ houseId: e.detail.value });
  },
  reload() {
    const { withLoading } = require("../../utils/ui");
    const api = require("../../services/api");
    withLoading(this, () =>
      Promise.all([api.houses(), api.myBindRequests()]).then(([houses, requests]) =>
        this.setData({
          items: (houses && houses.items) || [],
          requests: (requests && requests.items) || [],
        })
      )
    ).catch(() => {});
  },
  retry() {
    this.reload();
  },
  selectHouse(e) {
    const houseId = e.currentTarget.dataset.id;
    require("../../services/auth").setSelectedHouseId(houseId);
    wx.navigateTo({ url: `/pages/work-orders/create?houseId=${houseId}` });
  },
  applyBind() {
    const houseId = Number(this.data.houseId);
    if (!houseId) {
      this.setData({ error: "请填写要申请绑定的房屋 ID" });
      return;
    }
    const { withLoading } = require("../../utils/ui");
    withLoading(this, () =>
      require("../../services/api")
        .createBindRequest({ house_id: houseId, relation_type: "OWNER" })
        .then(() => this.reload())
    ).catch(() => {});
  },
});
