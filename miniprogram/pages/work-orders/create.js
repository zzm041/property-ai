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

function imageUploadType(type) {
  const normalized = String(type || "").toLowerCase();
  return {
    jpg: { mimeType: "image/jpeg", originalName: "repair.jpg" },
    jpeg: { mimeType: "image/jpeg", originalName: "repair.jpg" },
    png: { mimeType: "image/png", originalName: "repair.png" },
    webp: { mimeType: "image/webp", originalName: "repair.webp" },
  }[normalized];
}

Page({
  data: {
    houseId: "",
    houses: [],
    houseCursor: 0,
    houseLoading: false,
    houseError: "",
    selectedHouseLabel: "",
    text: "",
    fileIds: [],
    mediaHint: "",
    loading: false,
    error: "",
  },
  onLoad(query = {}) {
    const auth = require("../../services/auth");
    this.preferredHouseId = Number(query.houseId || auth.getSelectedHouseId() || 0);
    this.unloaded = false;
  },
  onShow() {
    return this.loadHouses(false);
  },
  onUnload() {
    this.unloaded = true;
    this.houseRequest = (this.houseRequest || 0) + 1;
  },
  reloadHouses() {
    return this.loadHouses(false);
  },
  loadMoreHouses() {
    if (!this.data.houseCursor) return Promise.resolve();
    return this.loadHouses(true);
  },
  loadHouses(more) {
    if (this.unloaded || this.data.loading || this.data.houseLoading) return Promise.resolve();
    const api = require("../../services/api");
    const { appendPage } = require("../../utils/paging");
    const { houseLabel } = require("../../utils/house-display");
    const after = more ? this.data.houseCursor : 0;
    const version = this.houseRequest = (this.houseRequest || 0) + 1;
    this.preferredHouseId = Number(this.data.houseId || this.preferredHouseId || 0);
    this.setData({ houseLoading: true, houseError: "", ...(more ? {} : { houses: [], houseCursor: 0, houseId: "", selectedHouseLabel: "" }) });
    return Promise.resolve().then(() => api.houses(after, 20)).then((payload) => {
      if (this.unloaded || version !== this.houseRequest) return;
      const result = appendPage(more ? this.data.houses : [], payload, after, 20, "relation_id");
      const houses = result.items.map(item => ({ ...item, addressLabel: houseLabel(item) }));
      const selected = houses.find(item => Number(item.house_id) === this.preferredHouseId);
      this.setData({ houses, houseCursor: result.nextCursor, houseId: selected ? String(selected.house_id) : "", selectedHouseLabel: selected ? selected.addressLabel : "" });
    }).catch((error) => {
      if (!this.unloaded && version === this.houseRequest) this.setData({ houseError: error.message || "房屋加载失败，请重试" });
    }).finally(() => {
      if (!this.unloaded && version === this.houseRequest) this.setData({ houseLoading: false });
    });
  },
  selectHouse(e) {
    if (this.data.loading || this.data.houseLoading) return;
    const id = Number(e.currentTarget.dataset.id);
    const item = this.data.houses.find(house => Number(house.house_id) === id);
    if (!Number.isSafeInteger(id) || id <= 0 || !item) return;
    this.preferredHouseId = id;
    this.setData({ houseId: String(id), selectedHouseLabel: item.addressLabel, error: "" });
  },
  manageHouses() {
    if (this.data.loading || this.data.houseLoading) return;
    wx.navigateTo({ url: "/pages/houses/houses" });
  },
  onText(e) {
    this.setData({ text: e.detail.value });
  },
  chooseImage() {
    if (this.data.loading || this.data.houseLoading) return;
    this._pick("image");
  },
  chooseAudio() {
    if (this.data.loading || this.data.houseLoading) return;
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
        wx.getImageInfo({
          src: file.tempFilePath,
          success: (info) => {
            const uploadType = imageUploadType(info.type);
            if (!uploadType) {
              this.setData({ error: "不支持该图片格式" });
              return;
            }
            this._upload(
              file.tempFilePath,
              uploadType.mimeType,
              file.size,
              uploadType.originalName
            );
          },
          fail: () => this.setData({ error: "无法识别图片格式" }),
        });
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
    if (this.data.loading || this.data.houseLoading || this.unloaded) return;
    const { withLoading } = require("../../utils/ui");
    return withLoading(this, async () => {
      const api = require("../../services/api");
      const auth = require("../../services/auth");
      const houseId = Number(this.data.houseId);
      if (!Number.isSafeInteger(houseId) || houseId <= 0 || !this.data.houses.some(item => Number(item.house_id) === houseId)) {
        throw new Error("请选择本人已绑定的房屋；没有房屋时请先申请绑定");
      }
      if (!this.data.text.trim()) throw new Error("请填写报修说明");
      const body = {
        house_id: houseId,
        text: this.data.text,
        file_ids: this.data.fileIds.slice(),
      };
      const signature = JSON.stringify(body);
      if (!this.pendingSubmission || this.pendingSubmission.signature !== signature) {
        this.pendingSubmission = { signature, key: `mp-${Date.now()}-${Math.random().toString(16).slice(2)}` };
      }
      const item = await api.createWorkOrder(body, this.pendingSubmission.key);
      const id = item.id || (item.work_order && item.work_order.id);
      auth.setSelectedHouseId(houseId);
      auth.setLastWorkOrderId(id);
      wx.navigateTo({ url: `/pages/work-orders/detail?id=${id}` });
    }).catch(() => {});
  },
});
