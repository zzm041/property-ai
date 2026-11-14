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
    logCount: 0,
    hasMoreLogs: false,
    systemLogs: [],
    systemGroups: [],
    systemLogCount: 0,
    systemExpanded: false,
    hasMoreSystemLogs: false,
    canAccept: false,
    canReject: false,
    canStart: false,
    canEscalate: false,
    canFinish: false,
    canSupplementReceiptMedia: false,
    supplementReceiptId: null,
    missingBeforeMedia: false,
    missingAfterMedia: false,
    rejectReason: "",
    feedback: "",
    problemType: "",
    method: "",
    materials: "",
    hasFee: false,
    feeAmount: "",
    followUp: "",
    receiptResolved: true,
    unresolvedReason: "",
    handlingIndex: 0,
    handlingNote: "",
    handlingOptions: [
      { value: "", label: "请选择（可选）" },
      { value: "SELF", label: "本人独立处理" },
      { value: "ASSISTED", label: "本人参与协作" },
      { value: "EXTERNAL", label: "由支援人员处理" },
    ],
    loading: false,
    error: "",
    onSiteMediaHint: "",
    beforeMediaHint: "",
    afterMediaHint: "",
    onSiteFileIds: [],
    beforeFileIds: [],
    afterFileIds: [],
    onSiteMedia: [],
    beforeMedia: [],
    afterMedia: [],
  },
  onLoad(query) {
    this.id = Number(query.id);
    this._handlingContext = null;
    this._handlingDraft = null;
    this.setData({ handlingIndex: 0, handlingNote: "" });
  },
  onShow() {
    this._hidden = false;
    return this.reload();
  },
  clearDetail() {
    this._detailGeneration = (this._detailGeneration || 0) + 1;
    this._businessLogs = []; this._systemLogs = [];
    this.setData({ item: null, workOrder: null, logs: [], loading: false, error: "",
      logCount: 0, hasMoreLogs: false, systemLogs: [], systemGroups: [], systemLogCount: 0,
      systemExpanded: false, hasMoreSystemLogs: false,
      canAccept: false, canReject: false, canStart: false, canEscalate: false,
      canFinish: false, canSupplementReceiptMedia: false, supplementReceiptId: null });
    return this._detailGeneration;
  },
  onHide() {
    if (!this._hidden && !this._handlingDraft) this._handlingDraft = { context: this._handlingContext,
      index: this.data.handlingIndex, note: this.data.handlingNote };
    this._hidden = true; this.clearDetail(); this.setData({ handlingIndex: 0, handlingNote: "" });
  },
  onUnload() {
    this._handlingDraft = null; this._handlingContext = null;
    this._hidden = true; this.clearDetail(); this.setData({ handlingIndex: 0, handlingNote: "" });
  },
  reload() {
    if (this._hidden) return Promise.resolve();
    const generation = this.clearDetail();
    const id = this.id;
    if (!Number.isSafeInteger(id) || id <= 0) {
      this.setData({ error: "工单编号无效" });
      return Promise.resolve();
    }
    const { workOrderOf } = require("../../utils/ui");
    this.setData({ loading: true });
    return Promise.resolve().then(() => {
      const api = require("../../services/api");
      return Promise.all([api.workOrder(id, "property"), api.me()]);
    }).then(([item, me]) => {
          if (generation !== this._detailGeneration) return;
          const workOrder = workOrderOf(item);
          if (!workOrder || Number(workOrder.id) !== id) throw new Error("工单已变化，请重新打开详情");
          const display = require("../../utils/work-order-display").presentDetail(item);
          const status = workOrder ? workOrder.status : "";
          const processReason = workOrder ? workOrder.process_reason : "NONE";
          const processes = Array.isArray(item.processes) ? item.processes : [];
          const receipts = Array.isArray(item.receipts) ? item.receipts : [];
          const media = Array.isArray(item.media) ? item.media : [];
          const latestProcess = processes.length ? processes[processes.length - 1] : null;
          if (!me || !Number.isSafeInteger(me.user_id) || me.user_id <= 0) throw new Error("当前身份无法确认，请重新登录");
          const history = require("../../utils/work-order-display").presentStaffHistory(item, me.user_id);
          this._businessLogs = history.businessLogs; this._systemLogs = history.systemLogs;
          const context = `${me.user_id}:${id}:${latestProcess ? latestProcess.id : 0}`;
          if (this._handlingDraft && this._handlingDraft.context === context) {
            this.setData({ handlingIndex: this._handlingDraft.index, handlingNote: this._handlingDraft.note });
          } else if (this._handlingContext !== context || this._handlingDraft) {
            this.setData({ handlingIndex: 0, handlingNote: "" });
          }
          this._handlingContext = context;
          this._handlingDraft = null;
          const latestReceipt = latestProcess
            ? receipts.find((receipt) => receipt.process_id === latestProcess.id)
            : null;
          const receiptMedia = latestReceipt
            ? media.filter((entry) => entry.receipt_id === latestReceipt.id)
            : [];
          const missingBeforeMedia =
            Boolean(latestReceipt) &&
            !receiptMedia.some((entry) => entry.taken_phase === "BEFORE");
          const missingAfterMedia =
            Boolean(latestReceipt) &&
            !receiptMedia.some((entry) => entry.taken_phase === "AFTER");
          require("../../services/auth").setLastWorkOrderId(this.id);
          this.setData({
            item,
            workOrder,
            statusLabel: display.statusLabel,
            reasonLabel: display.reasonLabel,
            logs: history.businessLogs.slice(0, 10), logCount: history.businessLogs.length,
            hasMoreLogs: history.businessLogs.length > 10,
            systemGroups: history.systemGroups, systemLogCount: history.systemLogs.length,
            canAccept: status === "ASSIGNED",
            canReject: status === "ASSIGNED",
            canStart: status === "ACCEPTED",
            canEscalate: status === "PROCESSING" && processReason === "NONE",
            canFinish: status === "PROCESSING" && processReason === "NONE",
            canSupplementReceiptMedia:
              status === "WAIT_CONFIRM" &&
              Boolean(latestReceipt) &&
              (missingBeforeMedia || missingAfterMedia),
            supplementReceiptId: latestReceipt ? latestReceipt.id : null,
            missingBeforeMedia,
            missingAfterMedia,
          });
        }).catch((error) => {
      if (generation === this._detailGeneration) this.setData({ error: error.message || "工单读取失败" });
    }).finally(() => {
      if (generation === this._detailGeneration) this.setData({ loading: false });
    });
  },
  showMoreLogs() {
    if (this._hidden || !this.data.workOrder) return;
    const logs = this._businessLogs.slice(0, this.data.logs.length + 10);
    this.setData({ logs, hasMoreLogs: logs.length < this._businessLogs.length });
  },
  toggleSystemLogs() {
    if (this._hidden || !this.data.workOrder) return;
    const systemExpanded = !this.data.systemExpanded;
    const systemLogs = systemExpanded ? this._systemLogs.slice(0, 10) : [];
    this.setData({ systemExpanded, systemLogs, hasMoreSystemLogs: systemExpanded && systemLogs.length < this._systemLogs.length });
  },
  showMoreSystemLogs() {
    if (this._hidden || !this.data.workOrder || !this.data.systemExpanded) return;
    const systemLogs = this._systemLogs.slice(0, this.data.systemLogs.length + 10);
    this.setData({ systemLogs, hasMoreSystemLogs: systemLogs.length < this._systemLogs.length });
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
  openEscalation() {
    if (!this.data.canEscalate || this.data.loading || !this.data.workOrder) return;
    wx.navigateTo({ url: `/pages/staff-orders/escalate?id=${this.data.workOrder.id}` });
  },
  chooseOnSiteImage() {
    this._chooseImage(
      "onSiteFileIds",
      "onSiteMedia",
      "onSiteMediaHint",
      "现场",
      "on-site"
    );
  },
  chooseBeforeImage() {
    this._chooseImage(
      "beforeFileIds",
      "beforeMedia",
      "beforeMediaHint",
      "处理前",
      "before"
    );
  },
  chooseAfterImage() {
    this._chooseImage(
      "afterFileIds",
      "afterMedia",
      "afterMediaHint",
      "处理后",
      "after"
    );
  },
  _chooseImage(fileIdsKey, mediaKey, hintKey, label, namePrefix) {
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
                  const media = this.data[mediaKey].concat({
                    fileId: record.id,
                    tempFilePath: file.tempFilePath,
                  });
                  this.setData({
                    [fileIdsKey]: fileIds,
                    [mediaKey]: media,
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
  previewPendingMedia(event) {
    const group = event.currentTarget.dataset.group;
    const current = event.currentTarget.dataset.src;
    const media = Array.isArray(this.data[group]) ? this.data[group] : [];
    const urls = media.map((item) => item.tempFilePath);
    if (!current || !urls.length) {
      return;
    }
    wx.previewImage({ current, urls });
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
          this.setData({
            feedback: "",
            onSiteFileIds: [],
            onSiteMedia: [],
            onSiteMediaHint: "",
          });
          return this.reload();
        })
    ).catch(() => {});
  },
  onHandlingMode(e) {
    const index = Number(e.detail.value);
    this.setData({ handlingIndex: Number.isInteger(index) && index >= 0 && index < this.data.handlingOptions.length ? index : 0,
      handlingNote: "" });
  },
  onHandlingNote(e) { this.setData({ handlingNote: e.detail.value }); },
  resolve() {
    const problemType = this.data.problemType.trim();
    const method = this.data.method.trim();
    const materials = this.data.materials.trim();
    const followUp = this.data.followUp.trim();
    const unresolvedReason = this.data.unresolvedReason.trim();
    const feeAmount = this.data.feeAmount.trim();
    const handlingMode = this.data.handlingOptions[this.data.handlingIndex].value;
    const handlingNote = this.data.handlingNote.trim();
    if (["ASSISTED", "EXTERNAL"].includes(handlingMode) && !handlingNote) {
      this.setData({ error: "请说明实际参与人员或单位及分工" });
      return;
    }
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
          ...(handlingMode ? { handling_mode: handlingMode, handling_note: handlingNote || null } : {}),
        })
        .then(() => {
          this._handlingDraft = null;
          this.setData({
            beforeFileIds: [],
            afterFileIds: [],
            beforeMedia: [],
            afterMedia: [],
            beforeMediaHint: "",
            afterMediaHint: "",
            handlingIndex: 0,
            handlingNote: "",
          });
          return this.reload();
        })
    ).catch(() => {});
  },
  supplementReceiptMedia() {
    if (this.data.loading || !this.data.supplementReceiptId) {
      return;
    }
    if (this.data.missingBeforeMedia && !this.data.beforeFileIds.length) {
      this.setData({ error: "请添加本周期处理前照片" });
      return;
    }
    if (this.data.missingAfterMedia && !this.data.afterFileIds.length) {
      this.setData({ error: "请添加本周期处理后照片" });
      return;
    }
    const { withLoading } = require("../../utils/ui");
    withLoading(this, () =>
      require("../../services/api")
        .supplementReceiptMedia(this.id, this.data.supplementReceiptId, {
          before_file_ids: this.data.beforeFileIds,
          after_file_ids: this.data.afterFileIds,
          version: this.data.workOrder.version,
        })
        .then(() => {
          this.setData({
            beforeFileIds: [],
            afterFileIds: [],
            beforeMedia: [],
            afterMedia: [],
            beforeMediaHint: "",
            afterMediaHint: "",
          });
          wx.showToast({ title: "照片已补传", icon: "success" });
          return this.reload();
        })
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
