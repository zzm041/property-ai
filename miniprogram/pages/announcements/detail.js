Page({
  data: { title: "", content: "", error: "" },
  onLoad(query) {
    this.setData({
      title: decodeURIComponent(query.title || ""),
      content: decodeURIComponent(query.content || ""),
    });
  },
});
