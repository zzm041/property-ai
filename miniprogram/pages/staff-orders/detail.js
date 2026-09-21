function imageUploadType(type, namePrefix) {
  const normalized = String(type || "").toLowerCase();
  return {
    jpg: { mimeType: "image/jpeg", originalName: `${namePrefix}.jpg` },
    jpeg: { mimeType: "image/jpeg", originalName: `${namePrefix}.jpg` },
    png: { mimeType: "image/png", originalName: `${namePrefix}.png` },
    webp: { mimeType: "image/webp", originalName: `${namePrefix}.webp` },
  }[normalized];
}

Page({
  data: {
    item: null,
    workOrder: null,
    logs: [],
    canAccept: false,
    canReject: false,
    canStart: false,
    canEscalate: false,
    canFinish: false,
    rejectReason: "",
    escalationReason: "",
    escalationIndex: 0,
    escalationOptions: [
      { value: "INTERNAL_UNRESOLVED", label: "内部无法解决，升级主管" },
      { value: "WAITING_PARTS", label: "等待配件" },
      { value: "WAITING_OWNER", label: "等待住户配合" },
      { value: "ESCALATED", label: "其他异常，升级主管" },
    ],
    feedback: "",
    problemType: "",
    method: "",
    materials: "",
    hasFee: false,
    feeAmount: "",
    followUp: "",
    receiptResolved: true,
    unresolvedReason: "",
    loading: false,
    error: "",
    onSiteMediaHint: "",
    beforeMediaHint: "",
    afterMediaHint: "",
    onSiteFileIds: [],
    beforeFileIds: [],
    afterFileIds: [],
  },
  onLoad(query) {
    this.id = Number(query.id);
    this.reload();
  },
  reload() {
    const { withLoading, workOrderOf } = require("../../utils/ui");
    withLoading(this, () =>
      require("../../services/api")
        .workOrder(this.id)
        .then((item) => {
          const workOrder = workOrderOf(item);
          const status = workOrder ? workOrder.status : "";
          const processReason = workOrder ? workOrder.process_reason : "NONE";
          require("../../services/auth").setLastWorkOrderId(this.id);
          this.setData({
            item,
            workOrder,
            logs: (item && item.logs) || [],
            canAccept: status === "ASSIGNED",
            canReject: status === "ASSIGNED",
            canStart: status === "ACCEPTED",
            canEscalate: status === "PROCESSING" && processReason === "NONE",
            canFinish: status === "PROCESSING" && processReason === "NONE",
          });
        })
    ).catch(() => {});
  },
  accept() {
    this._act("acceptWorkOrder");
  },
  onRejectReason(e) {
    this.setData({ rejectReason: e.detail.value });
  },
  reject() {
    const reason = this.data.rejectReason.trim();
    if (!reason) {
      this.setData({ error: "请填写拒单原因" });
      return;
    }
    const { withLoading } = require("../../utils/ui");
    withLoading(this, () =>
      require("../../services/api")
        .rejectWorkOrder(this.id, {
          reason,
          version: this.data.workOrder.version,
        })
        .then(() => {
          wx.showToast({ title: "已拒单，系统重新安排中", icon: "none" });
          setTimeout(() => wx.navigateBack(), 500);
        })
    ).catch(() => {});
  },
  openIncident() {
    const workOrder = this.data.workOrder;
    if (!workOrder || !workOrder.incident_id) {
      return;
    }
    wx.navigateTo({
      url: `/pages/supervisor/incidents?propertyId=${workOrder.property_id}&incidentId=${workOrder.incident_id}`,
    });
  },
  start() {
    this._act("startWorkOrder");
  },
  onFeedback(e) {
    this.setData({ feedback: e.detail.value });
  },
  onProblemType(e) {
    this.setData({ problemType: e.detail.value });
  },
  onMethod(e) {
    this.setData({ method: e.detail.value });
  },
  onMaterials(e) {
    this.setData({ materials: e.detail.value });
  },
  onHasFee(e) {
    this.setData({
      hasFee: e.detail.value,
      feeAmount: e.detail.value ? this.data.feeAmount : "",
    });
  },
  onFeeAmount(e) {
    this.setData({ feeAmount: e.detail.value });
  },
  onFollowUp(e) {
    this.setData({ followUp: e.detail.value });
  },
  onReceiptResolved(e) {
    this.setData({ receiptResolved: e.detail.value });
  },
  onUnresolvedReason(e) {
    this.setData({ unresolvedReason: e.detail.value });
  },
  onEscalationType(e) {
    this.setData({ escalationIndex: Number(e.detail.value) });
  },
  onEscalationReason(e) {
    this.setData({ escalationReason: e.detail.value });
  },
  escalate() {
    const reason = this.data.escalationReason.trim();
    if (!reason) {
      this.setData({ error: "请填写异常或等待原因" });
      return;
    }
    const selected = this.data.escalationOptions[this.data.escalationIndex];
    const { withLoading } = require("../../utils/ui");
    withLoading(this, () =>
      require("../../services/api")
        .escalateWorkOrder(this.id, {
          process_reason: selected.value,
          reason,
          version: this.data.workOrder.version,
        })
        .then(() => this.reload())
    ).catch(() => {});
  },
  chooseOnSiteImage() {
    this._chooseImage("onSiteFileIds", "onSiteMediaHint", "现场", "on-site");
  },
  chooseBeforeImage() {
    this._chooseImage("beforeFileIds", "beforeMediaHint", "处理前", "before");
  },
  chooseAfterImage() {
    this._chooseImage("afterFileIds", "afterMediaHint", "处理后", "after");
  },
  _chooseImage(fileIdsKey, hintKey, label, namePrefix) {
    wx.chooseMedia({
      count: 1,
      mediaType: ["image"],
      success: (result) => {
        const file = result.tempFiles[0];
        wx.getImageInfo({
          src: file.tempFilePath,
          success: (info) => {
            const uploadType = imageUploadType(info.type, namePrefix);
            if (!uploadType) {
              this.setData({ error: "不支持该图片格式" });
              return;
            }
            const api = require("../../services/api");
            const { withLoading } = require("../../utils/ui");
            withLoading(this, () =>
              api
                .registerAndUpload({
                  filePath: file.tempFilePath,
                  mimeType: uploadType.mimeType,
                  sizeBytes: file.size,
                  originalName: uploadType.originalName,
                })
                .then((record) => {
                  const fileIds = this.data[fileIdsKey].concat(record.id);
                  this.setData({
                    [fileIdsKey]: fileIds,
                    [hintKey]: `已添加 ${fileIds.length} 张${label}照片`,
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
  feedback() {
    const { withLoading } = require("../../utils/ui");
    withLoading(this, () =>
      require("../../services/api")
        .feedbackWorkOrder(this.id, {
          text: this.data.feedback,
          file_ids: this.data.onSiteFileIds,
          taken_phase: "ON_SITE",
          version: this.data.workOrder.version,
        })
        .then(() => {
          this.setData({ feedback: "", onSiteFileIds: [], onSiteMediaHint: "" });
          return this.reload();
        })
    ).catch(() => {});
  },
  resolve() {
    const problemType = this.data.problemType.trim();
    const method = this.data.method.trim();
    const materials = this.data.materials.trim();
    const followUp = this.data.followUp.trim();
    const unresolvedReason = this.data.unresolvedReason.trim();
    const feeAmount = this.data.feeAmount.trim();
    if (!problemType || !method) {
      this.setData({ error: "请填写问题类型和处理方式与过程" });
      return;
    }
    if (!this.data.receiptResolved && !unresolvedReason) {
      this.setData({ error: "未解决回执必须填写未解决原因" });
      return;
    }
    const parsedFeeAmount = Number(feeAmount);
    if (
      this.data.hasFee &&
      (!feeAmount || !Number.isFinite(parsedFeeAmount) || parsedFeeAmount <= 0)
    ) {
      this.setData({ error: "产生费用时请填写大于 0 的金额" });
      return;
    }
    const { withLoading } = require("../../utils/ui");
    withLoading(this, () =>
      require("../../services/api")
        .resolveWorkOrder(this.id, {
          problem_type: problemType,
          method,
          materials: materials || null,
          fee_flag: this.data.hasFee ? "RECORDED" : "NONE",
          fee_amount: this.data.hasFee ? feeAmount : null,
          resolved: this.data.receiptResolved,
          unresolved_reason: this.data.receiptResolved ? null : unresolvedReason,
          follow_up: followUp || null,
          before_file_ids: this.data.beforeFileIds,
          after_file_ids: this.data.afterFileIds,
          version: this.data.workOrder.version,
        })
        .then(() => this.reload())
    ).catch(() => {});
  },
  _act(name) {
    const { withLoading } = require("../../utils/ui");
    withLoading(this, () =>
      require("../../services/api")[name](this.id, {
        version: this.data.workOrder.version,
      }).then(() => this.reload())
    ).catch(() => {});
  },
});
