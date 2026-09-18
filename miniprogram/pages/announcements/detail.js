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
        .announcements(this.propertyId)
        .then((data) => {
          const items = (data && data.items) || [];
          const item = items.find((row) => row.id === this.id);
          if (!item) {
            throw new Error("公告不存在、未发布或无权查看");
          }
          this.setData({
            title: item.title || "",
            content: item.content || "",
            status: item.status || "",
          });
        })
    ).catch(() => {});
  },
});
