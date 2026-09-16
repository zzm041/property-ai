Page({
  data: { question: "", answer: "", citations: [], error: "", propertyId: "", loading: false },
  onLoad(query) {
    if (query.propertyId) {
      this.setData({ propertyId: query.propertyId });
    }
  },
  onQuestion(e) {
    this.setData({ question: e.detail.value });
  },
  onProperty(e) {
    this.setData({ propertyId: e.detail.value });
  },
  ask() {
    const { withLoading } = require("../../utils/ui");
    withLoading(this, () =>
      require("../../services/api")
        .queryAssistant({
          question: this.data.question,
          property_id: Number(this.data.propertyId),
        })
        .then((data) =>
          this.setData({
            answer: data.answer || data.status,
            citations: data.citations || [],
          })
        )
    ).catch(() => {});
  },
});
