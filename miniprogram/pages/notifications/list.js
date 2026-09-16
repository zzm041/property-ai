Page({
  data: { items: [], unread: 0, loading: false, error: "" },
  onShow() {
    const { withLoading } = require("../../utils/ui");
    withLoading(this, () =>
      require("../../services/api")
        .notifications()
        .then((data) =>
          this.setData({
            items: (data && data.items) || [],
            unread: (data && data.unread_count) || 0,
          })
        )
    ).catch(() => {});
  },
  read(e) {
    require("../../services/api")
      .readNotification(e.currentTarget.dataset.id)
      .then(() => this.onShow())
      .catch((error) => this.setData({ error: error.message }));
  },
});
