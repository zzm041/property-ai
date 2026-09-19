Page({
  data: {
    item: null,
    workOrder: null,
    autoDispatched: false,
    latestReceipt: null,
    media: [],
    logs: [],
    canConfirm: false,
    canReopen: false,
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
    withLoading(this, () =>
      require("../../services/api")
        .workOrder(this.id)
        .then((item) => {
          const workOrder = workOrderOf(item);
          const status = workOrder ? workOrder.status : "";
          const assignments = (item && item.assignments) || [];
          const receipts = (item && item.receipts) || [];
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
            media: (item && item.media) || [],
            logs: (item && item.logs) || [],
            canConfirm: status === "WAIT_CONFIRM",
            canReopen: status === "WAIT_CONFIRM",
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
      this._pollTimer = setTimeout(() => this.reload(), 1000);
    }
  },
  confirm() {
    const { withLoading } = require("../../utils/ui");
    withLoading(this, () =>
      require("../../services/api")
        .confirmWorkOrder(this.id, { version: this.data.workOrder.version })
        .then(() => this.reload())
    ).catch(() => {});
  },
  reopen() {
    const { withLoading } = require("../../utils/ui");
    withLoading(this, () =>
      require("../../services/api")
        .reopenWorkOrder(this.id, {
          reason: "未解决",
          version: this.data.workOrder.version,
        })
        .then(() => this.reload())
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
