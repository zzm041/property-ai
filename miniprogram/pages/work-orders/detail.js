function imageUploadType(type) {
  const normalized = String(type || "").toLowerCase();
  return {
    jpg: { mimeType: "image/jpeg", originalName: "resident-append.jpg" },
    jpeg: { mimeType: "image/jpeg", originalName: "resident-append.jpg" },
    png: { mimeType: "image/png", originalName: "resident-append.png" },
    webp: { mimeType: "image/webp", originalName: "resident-append.webp" },
  }[normalized];
}

Page({
  data: {
    item: null,
    workOrder: null,
    autoDispatched: false,
    latestReceipt: null,
    processHistory: [],
    reopenReason: "",
    media: [],
    logs: [],
    feedbacks: [],
    latestRating: null,
    canConfirm: false,
    canReopen: false,
    canAppend: false,
    canCancel: false,
    canRate: false,
    appendText: "",
    appendFileIds: [],
    appendMediaHint: "",
    cancelReason: "",
    ratingOptions: [1, 2, 3, 4, 5],
    ratingIndex: 4,
    ratingComment: "",
    actionNotice: "",
    loading: false,
    error: "",
  },
  onLoad(query) {
    this.id = Number(query.id);
    this._pollCount = 0;
    this.reload();
  },
  onUnload() {
    if (this._pollTimer) {
      clearTimeout(this._pollTimer);
    }
    if (this._audio) {
      this._audio.destroy();
      this._audio = null;
    }
  },
  reload() {
    const { withLoading, workOrderOf } = require("../../utils/ui");
    return withLoading(this, () =>
      require("../../services/api")
        .workOrder(this.id)
        .then((item) => {
          const workOrder = workOrderOf(item);
          const status = workOrder ? workOrder.status : "";
          const assignments = (item && item.assignments) || [];
          const receipts = (item && item.receipts) || [];
          const feedbacks = (item && item.feedbacks) || [];
          const processHistory = ((item && item.processes) || []).map((process) => ({
            ...process,
            receipt: receipts.find((receipt) => receipt.process_id === process.id) || null,
            feedbacks: feedbacks.filter((feedback) => feedback.process_id === process.id),
          }));
          const ratings = feedbacks.filter((feedback) => feedback.rating != null);
          const latestRating = ratings.length ? ratings[ratings.length - 1] : null;
          const currentAssignment = assignments.length
            ? assignments[assignments.length - 1]
            : null;
          require("../../services/auth").setLastWorkOrderId(this.id);
          this.setData({
            item,
            workOrder,
            autoDispatched:
              Boolean(currentAssignment) &&
              currentAssignment.reason === "AUTO_DISPATCH" &&
              currentAssignment.assigned_by_user_id == null,
            latestReceipt: receipts.length ? receipts[receipts.length - 1] : null,
            processHistory,
            media: (item && item.media) || [],
            logs: (item && item.logs) || [],
            feedbacks,
            latestRating,
            canConfirm: status === "WAIT_CONFIRM",
            canReopen: status === "WAIT_CONFIRM",
            canAppend: !["COMPLETED", "CANCELLED", "CLOSED"].includes(status),
            canCancel:
              status === "PENDING_ANALYSIS" || status === "PENDING_ASSIGNMENT",
            canRate:
              (status === "COMPLETED" || status === "CLOSED") && !latestRating,
          });
          this._schedulePendingRefresh(status);
        })
    ).catch(() => {});
  },
  _schedulePendingRefresh(status) {
    if (this._pollTimer) {
      clearTimeout(this._pollTimer);
      this._pollTimer = null;
    }
    if (
      (status === "PENDING_ANALYSIS" || status === "PENDING_ASSIGNMENT") &&
      this._pollCount < 20
    ) {
      this._pollCount += 1;
      this._pollTimer = setTimeout(() => {
        if (!this.data.loading) {
          this.reload();
        }
      }, 1000);
    }
  },
  _stopPendingRefresh() {
    if (this._pollTimer) {
      clearTimeout(this._pollTimer);
      this._pollTimer = null;
    }
  },
  onAppendText(e) {
    this.setData({ appendText: e.detail.value });
  },
  chooseAppendImage() {
    wx.chooseMedia({
      count: 1,
      mediaType: ["image"],
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
            const { withLoading } = require("../../utils/ui");
            withLoading(this, () =>
              require("../../services/api")
                .registerAndUpload({
                  filePath: file.tempFilePath,
                  mimeType: uploadType.mimeType,
                  sizeBytes: file.size,
                  originalName: uploadType.originalName,
                })
                .then((record) => {
                  const fileIds = this.data.appendFileIds.concat(record.id);
                  this.setData({
                    appendFileIds: fileIds,
                    appendMediaHint: `已添加 ${fileIds.length} 张补充图片`,
                  });
                })
            ).catch(() => {});
          },
          fail: () => this.setData({ error: "无法识别图片格式" }),
        });
      },
      fail: (error) => this.setData({ error: error.errMsg || "选择图片失败" }),
    });
  },
  appendContent() {
    if (this.data.loading) {
      return;
    }
    const text = this.data.appendText.trim();
    if (!text && !this.data.appendFileIds.length) {
      this.setData({ error: "请填写补充说明或添加图片" });
      return;
    }
    this._stopPendingRefresh();
    const { withLoading } = require("../../utils/ui");
    withLoading(this, () =>
      require("../../services/api")
        .appendWorkOrder(this.id, {
          text: text || null,
          file_ids: this.data.appendFileIds,
          taken_phase: "REPORT",
          version: this.data.workOrder.version,
        })
        .then(() => {
          this.setData({
            appendText: "",
            appendFileIds: [],
            appendMediaHint: "",
            actionNotice: "补充内容已保存",
          });
          wx.showToast({ title: "补充成功", icon: "success" });
          return this.reload();
        })
    ).catch(() => {});
  },
  onCancelReason(e) {
    this.setData({ cancelReason: e.detail.value });
  },
  cancel() {
    if (this.data.loading || this._cancelModalOpen) {
      return;
    }
    this._cancelModalOpen = true;
    wx.showModal({
      title: "确认撤销工单",
      content: "撤销后将不能继续补充或处理该工单。",
      confirmText: "确认撤销",
      success: (result) => {
        this._cancelModalOpen = false;
        if (result.confirm) {
          this._submitCancel();
        }
      },
      fail: () => {
        this._cancelModalOpen = false;
        this.setData({ error: "无法打开撤销确认" });
      },
    });
  },
  _submitCancel() {
    this._stopPendingRefresh();
    const reason = this.data.cancelReason.trim();
    const { withLoading } = require("../../utils/ui");
    withLoading(this, () =>
      require("../../services/api")
        .cancelWorkOrder(this.id, {
          reason: reason || null,
          version: this.data.workOrder.version,
        })
        .then(() => {
          this.setData({ cancelReason: "", actionNotice: "工单已撤销" });
          wx.showToast({ title: "已撤销", icon: "success" });
          return this.reload();
        })
    ).catch(() => {});
  },
  onRatingChange(e) {
    this.setData({ ratingIndex: Number(e.detail.value) });
  },
  onRatingComment(e) {
    this.setData({ ratingComment: e.detail.value });
  },
  rate() {
    if (this.data.loading) {
      return;
    }
    this._stopPendingRefresh();
    const rating = this.data.ratingOptions[this.data.ratingIndex];
    const comment = this.data.ratingComment.trim();
    const { withLoading } = require("../../utils/ui");
    withLoading(this, () =>
      require("../../services/api")
        .rateWorkOrder(this.id, { rating, comment: comment || null })
        .then(() => {
          this.setData({ ratingComment: "", actionNotice: "评价已保存" });
          wx.showToast({ title: "评价成功", icon: "success" });
          return this.reload();
        })
    ).catch(() => {});
  },
  confirm() {
    if (this.data.loading) {
      return;
    }
    this._stopPendingRefresh();
    const { withLoading } = require("../../utils/ui");
    return withLoading(this, () =>
      require("../../services/api")
        .confirmWorkOrder(this.id, { version: this.data.workOrder.version })
        .then(() => this.reload())
    ).catch(() => {});
  },
  onReopenReason(e) {
    this.setData({ reopenReason: e.detail.value });
  },
  reopen() {
    if (this.data.loading) {
      return;
    }
    const reason = this.data.reopenReason.trim();
    if (!reason || reason.length > 255) {
      this.setData({ error: "请填写未解决的具体情况（1至255字）" });
      return;
    }
    this._stopPendingRefresh();
    const { withLoading } = require("../../utils/ui");
    return withLoading(this, () =>
      require("../../services/api")
        .reopenWorkOrder(this.id, {
          reason,
          version: this.data.workOrder.version,
        })
        .then(() => {
          this.setData({ reopenReason: "", actionNotice: "未解决反馈已保存，物业将继续处理" });
          return this.reload();
        })
    ).catch(() => {});
  },
  previewMedia(e) {
    const fileId = Number(e.currentTarget.dataset.fileId);
    const { withLoading } = require("../../utils/ui");
    withLoading(this, () =>
      require("../../services/api")
        .downloadFile({ fileId })
        .then(
          (filePath) =>
            new Promise((resolve, reject) => {
              wx.previewImage({
                urls: [filePath],
                current: filePath,
                success: resolve,
                fail: () => reject(new Error("图片预览失败")),
              });
            })
        )
    ).catch(() => {});
  },
  playMedia(e) {
    const fileId = Number(e.currentTarget.dataset.fileId);
    const { withLoading } = require("../../utils/ui");
    withLoading(this, () =>
      require("../../services/api")
        .downloadFile({ fileId })
        .then((filePath) => {
          if (this._audio) {
            this._audio.destroy();
          }
          this._audio = wx.createInnerAudioContext();
          this._audio.src = filePath;
          this._audio.onError(() => this.setData({ error: "语音播放失败" }));
          this._audio.play();
        })
    ).catch(() => {});
  },
});
