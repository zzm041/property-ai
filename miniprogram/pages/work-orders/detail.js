Page({
  data: {
    item: null,
    workOrder: null,
    autoDispatched: false,
    latestReceipt: null,
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
});
