Page({
  data: { title: "", content: "", status: "", error: "", loading: false },
  onLoad(query) {
    this.id = Number(query.id);
    this.propertyId = query.propertyId || "";
    if (!this.id || !this.propertyId) {
      this.setData({ error: "缺少公告或小区" });
      return;
    }
    this.reload();
  },
  reload() {
    const { withLoading } = require("../../utils/ui");
    withLoading(this, () =>
      require("../../services/api")
        .announcement(this.propertyId, this.id)
        .then((item) => {
          this.setData({
            title: item.title || "",
            content: item.content || "",
            status: item.status || "",
          });
        })
    ).catch(() => {});
  },
});
