Page({
  data: { question: "", answer: "", citations: [], error: "", propertyId: "" },
  onQuestion(e) { this.setData({ question: e.detail.value }); },
  onProperty(e) { this.setData({ propertyId: e.detail.value }); },
  ask() {
    this.setData({ error: "" });
    require("../../services/api").queryAssistant({
      question: this.data.question,
      property_id: Number(this.data.propertyId),
    }).then((data) => this.setData({
      answer: data.answer || data.status,
      citations: data.citations || [],
    })).catch((error) => this.setData({ error: error.message }));
  },
});
