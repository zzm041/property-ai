Page({
  data: {
    item: null,
    workOrder: null,
    logs: [],
    canAccept: false,
    canStart: false,
    canFinish: false,
    feedback: "",
    problemType: "",
    method: "",
    materials: "",
    hasFee: false,
    feeAmount: "",
    followUp: "",
    loading: false,
    error: "",
    mediaHint: "",
    fileIds: [],
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
          require("../../services/auth").setLastWorkOrderId(this.id);
          this.setData({
            item,
            workOrder,
            logs: (item && item.logs) || [],
            canAccept: status === "ASSIGNED",
            canStart: status === "ACCEPTED",
            canFinish: status === "PROCESSING",
          });
        })
    ).catch(() => {});
  },
  accept() {
    this._act("acceptWorkOrder");
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
  chooseAfterImage() {
    wx.chooseMedia({
      count: 1,
      mediaType: ["image"],
      success: (result) => {
        const file = result.tempFiles[0];
        const api = require("../../services/api");
        const { withLoading } = require("../../utils/ui");
        withLoading(this, () =>
          api
            .registerAndUpload({
              filePath: file.tempFilePath,
              mimeType: "image/jpeg",
              sizeBytes: file.size,
              originalName: "after.jpg",
            })
            .then((record) => {
              const fileIds = this.data.fileIds.concat(record.id);
              this.setData({ fileIds, mediaHint: `已添加 ${fileIds.length} 张完工照片` });
            })
        ).catch(() => {});
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
          file_ids: this.data.fileIds,
          taken_phase: "AFTER",
          version: this.data.workOrder.version,
        })
        .then(() => this.reload())
    ).catch(() => {});
  },
  resolve() {
    const problemType = this.data.problemType.trim();
    const method = this.data.method.trim();
    const materials = this.data.materials.trim();
    const followUp = this.data.followUp.trim();
    const feeAmount = this.data.feeAmount.trim();
    if (!problemType || !method) {
      this.setData({ error: "请填写问题类型和处理方式与过程" });
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
          resolved: true,
          follow_up: followUp || null,
          file_ids: this.data.fileIds,
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
