Page({
  data: { item: null, loading: false, error: "" },
  onLoad(query) {
    this.id = Number(query.id);
    this.reload();
  },
  reload() {
    const { withLoading } = require("../../utils/ui");
    withLoading(this, () =>
      require("../../services/api")
        .workOrder(this.id)
        .then((item) => this.setData({ item }))
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
