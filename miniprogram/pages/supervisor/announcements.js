Page({
  data: { title: "", content: "", propertyId: "", error: "" },
  onField(e) { this.setData({ [e.currentTarget.dataset.field]: e.detail.value }); },
  create() {
    require("../../services/api").createAnnouncement({
      property_id: Number(this.data.propertyId),
      title: this.data.title,
      content: this.data.content,
    }).catch((error) => this.setData({ error: error.message }));
  },
});
