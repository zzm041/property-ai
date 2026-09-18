Page({
  data: {
    item: null,
    workOrder: null,
    logs: [],
    canAccept: false,
    canStart: false,
    canFinish: false,
    feedback: "",
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
        })
        .then(() => this.reload())
    ).catch(() => {});
  },
  resolve() {
    const { withLoading } = require("../../utils/ui");
    withLoading(this, () =>
      require("../../services/api")
        .resolveWorkOrder(this.id, {
          problem_type: "repair",
          method: "on_site",
          resolved: true,
          file_ids: this.data.fileIds,
        })
        .then(() => this.reload())
    ).catch(() => {});
  },
  _act(name) {
    const { withLoading } = require("../../utils/ui");
    withLoading(this, () =>
      require("../../services/api")[name](this.id).then(() => this.reload())
    ).catch(() => {});
  },
});
