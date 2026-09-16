Page({
  data: { title: "", content: "", propertyId: "", draftId: "", loading: false, error: "" },
  onLoad(query) {
    if (query.propertyId) {
      this.setData({ propertyId: query.propertyId });
    }
  },
  onField(e) {
    this.setData({ [e.currentTarget.dataset.field]: e.detail.value });
  },
  create() {
    const { withLoading } = require("../../utils/ui");
    withLoading(this, () =>
      require("../../services/api")
        .createAnnouncement({
          property_id: Number(this.data.propertyId),
          title: this.data.title,
          content: this.data.content,
        })
        .then((item) => this.setData({ draftId: item.id, error: "" }))
    ).catch(() => {});
  },
  publish() {
    if (!this.data.draftId) {
      this.setData({ error: "请先保存草稿" });
      return;
    }
    const { withLoading } = require("../../utils/ui");
    const api = require("../../services/api");
    withLoading(this, () =>
      api
        .submitAnnouncementReview(this.data.draftId)
        .then(() => api.publishAnnouncement(this.data.draftId, { decision: "APPROVE" }))
    ).catch(() => {});
  },
});
