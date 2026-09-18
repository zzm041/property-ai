Page({
  data: {
    item: null,
    workOrder: null,
    logs: [],
    canConfirm: false,
    canReopen: false,
    loading: false,
    error: "",
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
            canConfirm: status === "WAIT_CONFIRM",
            canReopen: status === "WAIT_CONFIRM",
          });
        })
    ).catch(() => {});
  },
  confirm() {
    const { withLoading } = require("../../utils/ui");
    withLoading(this, () =>
      require("../../services/api")
        .confirmWorkOrder(this.id)
        .then(() => this.reload())
    ).catch(() => {});
  },
  reopen() {
    const { withLoading } = require("../../utils/ui");
    withLoading(this, () =>
      require("../../services/api")
        .reopenWorkOrder(this.id, { reason: "未解决" })
        .then(() => this.reload())
    ).catch(() => {});
  },
});
