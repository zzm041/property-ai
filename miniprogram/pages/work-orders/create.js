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

const MAX_ANALYSIS_IMAGE_DIMENSION = 1024;

Page({
  data: {
    houseId: "",
    houses: [],
    houseCursor: 0,
    houseLoading: false,
    houseError: "",
    selectedHouseLabel: "",
    text: "",
    contactPhone: "",
    contactConfirmed: false,
    fileIds: [],
    mediaHint: "",
    recording: false,
    recordingHint: "",
    loading: false,
    error: "",
  },
  onLoad(query = {}) {
    const auth = require("../../services/auth");
    this.preferredHouseId = Number(query.houseId || auth.getSelectedHouseId() || 0);
    this.unloaded = false;
    this._bindRecorder();
  },
  onShow() {
    if (!this.contactDefaultLoaded && !this.contactEdited) {
      this.contactDefaultLoaded = true;
      require('../../services/api').contactDefault().then(result => {
        if (!this.unloaded && !this.contactEdited) this.setData({ contactPhone: result.phone || '', contactConfirmed: false });
      }).catch(() => { if (!this.unloaded) this.setData({ error: '联系电话默认值读取失败，请手动填写并确认' }); });
    }
    return this.loadHouses(false);
  },
  onContact(e) {
    this.contactEdited = true;
    this.setData({ contactPhone: e.detail.value, contactConfirmed: false });
  },
  confirmContact(e) {
    this.setData({ contactConfirmed: e.detail.value.includes('confirmed') });
  },
  onUnload() {
    this.unloaded = true;
    this.houseRequest = (this.houseRequest || 0) + 1;
    this._discardRecording = true;
    if (this._recording && this._recorder) this._recorder.stop();
    this._unbindRecorder();
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
            this._prepareImage(file, info, uploadType)
              .then((prepared) => this._upload(
                prepared.filePath,
                prepared.mimeType,
                prepared.sizeBytes,
                prepared.originalName
              ))
              .catch((error) => this.setData({ error: error.message || "图片处理失败" }));
          },
          fail: () => this.setData({ error: "无法识别图片格式" }),
        });
      },
      fail: (error) => this.setData({ error: error.errMsg || "选择图片失败" }),
    });
  },
  _bindRecorder() {
    if (typeof wx.getRecorderManager !== "function") return;
    this._recorder = wx.getRecorderManager();
    this._onRecorderStart = () => {
      if (this.unloaded) return;
      this._recording = true;
      this.setData({ recording: true, recordingHint: "正在录音，最长30秒", error: "" });
    };
    this._onRecorderStop = (result) => {
      const discard = this._discardRecording || this.unloaded;
      this._recording = false;
      this._discardRecording = false;
      if (!this.unloaded) this.setData({ recording: false });
      if (discard) return;
      const filePath = result && result.tempFilePath;
      const sizeBytes = Number(result && result.fileSize);
      const duration = Number(result && result.duration);
      if (!filePath || !Number.isSafeInteger(sizeBytes) || sizeBytes <= 0 || !Number.isFinite(duration) || duration < 500) {
        this.setData({ recordingHint: "", error: "录音过短或录音文件无效，请重新录制" });
        return;
      }
      this.setData({ recordingHint: `录音完成（${Math.ceil(duration / 1000)}秒），正在上传` });
      this._upload(filePath, "audio/mpeg", sizeBytes, "repair-recording.mp3", () => {
        if (!this.unloaded) this.setData({ recordingHint: "录音已添加" });
      });
    };
    this._onRecorderError = (error) => {
      this._recording = false;
      if (!this.unloaded) {
        this.setData({
          recording: false,
          recordingHint: "",
          error: (error && error.errMsg) || "录音失败，请检查麦克风权限后重试",
        });
      }
    };
    this._recorder.onStart(this._onRecorderStart);
    this._recorder.onStop(this._onRecorderStop);
    this._recorder.onError(this._onRecorderError);
  },
  _unbindRecorder() {
    if (!this._recorder) return;
    if (typeof this._recorder.offStart === "function") this._recorder.offStart(this._onRecorderStart);
    if (typeof this._recorder.offStop === "function") this._recorder.offStop(this._onRecorderStop);
    if (typeof this._recorder.offError === "function") this._recorder.offError(this._onRecorderError);
  },
  startRecording() {
    if (this.data.loading || this.data.houseLoading || this.data.recording) return;
    if (!this._recorder) {
      this.setData({ error: "当前微信版本不支持录音，请升级微信后重试" });
      return;
    }
    this._discardRecording = false;
    wx.authorize({
      scope: "scope.record",
      success: () => this._recorder.start({
        duration: 30000,
        sampleRate: 16000,
        numberOfChannels: 1,
        encodeBitRate: 48000,
        format: "mp3",
      }),
      fail: () => this.setData({ error: "未获得麦克风权限，请在小程序设置中允许录音后重试" }),
    });
  },
  stopRecording() {
    if (!this.data.recording || !this._recorder) return;
    this._recorder.stop();
  },
  _prepareImage(file, info, uploadType) {
    const width = Number(info.width || 0);
    const height = Number(info.height || 0);
    if (Math.max(width, height) <= MAX_ANALYSIS_IMAGE_DIMENSION) {
      return Promise.resolve({
        filePath: file.tempFilePath,
        mimeType: uploadType.mimeType,
        sizeBytes: file.size,
        originalName: uploadType.originalName,
      });
    }
    if (typeof wx.compressImage !== "function") {
      return Promise.reject(new Error("图片尺寸过大，当前微信版本无法安全压缩，请选择最长边不超过1024像素的图片"));
    }
    const scale = MAX_ANALYSIS_IMAGE_DIMENSION / Math.max(width, height);
    const compressedWidth = Math.max(1, Math.round(width * scale));
    const compressedHeight = Math.max(1, Math.round(height * scale));
    return new Promise((resolve, reject) => {
      wx.compressImage({
        src: file.tempFilePath,
        quality: 85,
        compressedWidth,
        compressedHeight,
        success: (result) => {
          wx.getImageInfo({
            src: result.tempFilePath,
            success: (compressedInfo) => {
              const compressedType = imageUploadType(compressedInfo.type);
              if (!compressedType || Math.max(Number(compressedInfo.width), Number(compressedInfo.height)) > MAX_ANALYSIS_IMAGE_DIMENSION) {
                reject(new Error("图片压缩结果不符合分析尺寸限制"));
                return;
              }
              wx.getFileSystemManager().stat({
                path: result.tempFilePath,
                success: (statResult) => resolve({
                  filePath: result.tempFilePath,
                  mimeType: compressedType.mimeType,
                  sizeBytes: statResult.stats.size,
                  originalName: compressedType.originalName,
                }),
                fail: () => reject(new Error("无法读取压缩后的图片")),
              });
            },
            fail: () => reject(new Error("无法识别压缩后的图片")),
          });
        },
        fail: () => reject(new Error("图片尺寸过大且压缩失败，请重新选择图片")),
      });
    });
  },
  _upload(filePath, mimeType, sizeBytes, originalName, onUploaded) {
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
          if (onUploaded) onUploaded(file);
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
      const contact = require('../../utils/contact-phone').normalizeContact(this.data.contactPhone);
      if (!contact) throw new Error('请填写合法联系电话：手机号或含国际区号的号码');
      if (!this.data.contactConfirmed) throw new Error('请确认本次联系电话');
      const body = {
        house_id: houseId,
        contact_phone: contact,
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
