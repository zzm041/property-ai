Page({
  data: { houseId: "", text: "", error: "" },
  onHouse(e) { this.setData({ houseId: e.detail.value }); },
  onText(e) { this.setData({ text: e.detail.value }); },
  chooseImage() {
    wx.chooseMedia({ count: 1, mediaType: ["image"] });
  },
  chooseAudio() {
    wx.showToast({ title: "请先上传拿 file_id", icon: "none" });
  },
  async submit() {
    this.setData({ error: "" });
    try {
      const api = require("../../services/api");
      const item = await api.createWorkOrder({
        house_id: Number(this.data.houseId),
        text: this.data.text,
      });
      const ids = wx.getStorageSync("work_order_ids") || [];
      ids.unshift(item.id);
      wx.setStorageSync("work_order_ids", ids);
      wx.navigateTo({ url: `/pages/work-orders/detail?id=${item.id}` });
    } catch (error) {
      this.setData({ error: error.message });
    }
  },
});
