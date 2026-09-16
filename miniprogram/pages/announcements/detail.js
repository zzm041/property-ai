Page({
  data: { title: "", content: "" },
  onLoad(query) {
    this.setData({ title: query.title || "", content: query.content || "" });
  },
});
