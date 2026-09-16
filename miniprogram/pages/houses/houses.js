Page({
  data: { items: [], loading: false, error: "" },
  onShow() {
    const { withLoading } = require("../../utils/ui");
    withLoading(this, () =>
      require("../../services/api")
        .houses()
        .then((data) => this.setData({ items: (data && data.items) || [] }))
    ).catch(() => {});
  },
  retry() {
    this.onShow();
  },
});
