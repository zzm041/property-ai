Page({
  data: { items: [], error: "" },
  onShow() {
    require("../../services/api").houses()
      .then((data) => this.setData({ items: data.items || data }))
      .catch((error) => this.setData({ error: error.message }));
  },
});
