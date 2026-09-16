Page({
  data: { items: [], unread: 0, error: "" },
  onShow() {
    require("../../services/api").notifications()
      .then((data) => this.setData({ items: data.items || [], unread: data.unread_count || 0 }))
      .catch((error) => this.setData({ error: error.message }));
  },
  read(e) {
    require("../../services/api").readNotification(e.currentTarget.dataset.id)
      .then(() => this.onShow())
      .catch((error) => this.setData({ error: error.message }));
  },
});
