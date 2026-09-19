function audioMimeType(filename) {
  const extension = String(filename || "").split(".").pop().toLowerCase();
  return {
    mp3: "audio/mpeg",
    wav: "audio/wav",
    m4a: "audio/mp4",
    mp4: "audio/mp4",
    webm: "audio/webm",
  }[extension];
}

Page({
  data: {
    houseId: "",
    text: "",
    fileIds: [],
    mediaHint: "",
    loading: false,
    error: "",
  },
  onLoad(query) {
    const auth = require("../../services/auth");
    const houseId = query.houseId || auth.getSelectedHouseId() || "";
    this.setData({ houseId });
  },
  onHouse(e) {
    this.setData({ houseId: e.detail.value });
  },
  onText(e) {
    this.setData({ text: e.detail.value });
  },
  chooseImage() {
    this._pick("image");
  },
  chooseAudio() {
    wx.chooseMessageFile({
      count: 1,
      type: "file",
      extension: ["mp3", "wav", "m4a", "mp4", "webm"],
      success: (result) => {
        const file = result.tempFiles[0];
        const mimeType = audioMimeType(file.name);
        if (!mimeType) {
          this.setData({ error: "不支持该音频格式" });
          return;
        }
        this._upload(file.path, mimeType, file.size, file.name);
      },
      fail: (error) => this.setData({ error: error.errMsg || "选择音频失败" }),
    });
  },
  _pick(mediaType) {
    wx.chooseMedia({
      count: 1,
      mediaType: [mediaType],
      success: (result) => {
        const file = result.tempFiles[0];
        this._upload(file.tempFilePath, "image/jpeg", file.size, "repair.jpg");
      },
      fail: (error) => this.setData({ error: error.errMsg || "选择图片失败" }),
    });
  },
  _upload(filePath, mimeType, sizeBytes, originalName) {
    const api = require("../../services/api");
    const { withLoading } = require("../../utils/ui");
    withLoading(this, () =>
      api
        .registerAndUpload({
          filePath,
          mimeType,
          sizeBytes,
          originalName,
        })
        .then((file) => {
          const fileIds = this.data.fileIds.concat(file.id);
          this.setData({
            fileIds,
            mediaHint: `已添加 ${fileIds.length} 个附件`,
          });
        })
    ).catch(() => {});
  },
  async submit() {
    const { withLoading } = require("../../utils/ui");
    withLoading(this, async () => {
      const api = require("../../services/api");
      const auth = require("../../services/auth");
      const houseId = Number(this.data.houseId);
      if (!houseId || !this.data.text) {
        throw new Error("请填写房屋和报修说明");
      }
      const item = await api.createWorkOrder({
        house_id: houseId,
        text: this.data.text,
        file_ids: this.data.fileIds,
      });
      const id = item.id || (item.work_order && item.work_order.id);
      auth.setSelectedHouseId(houseId);
      auth.setLastWorkOrderId(id);
      wx.navigateTo({ url: `/pages/work-orders/detail?id=${id}` });
    }).catch(() => {});
  },
});
