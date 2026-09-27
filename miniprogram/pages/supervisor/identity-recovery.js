const api = require("../../services/api");
const { withLoading } = require("../../utils/ui");

Page({
  data: { loading: false, error: "", items: [], reasonById: {} },
  onShow() { this.load(); },
  load() {
    return withLoading(this, () => api.identityRecoveryCases().then((payload) => {
      this.setData({ items: payload.items || [], error: "" });
    })).catch(() => {});
  },
  onReason(event) {
    const reasonById = { ...this.data.reasonById, [event.currentTarget.dataset.id]: event.detail.value };
    this.setData({ reasonById });
  },
  review(event) {
    const id = Number(event.currentTarget.dataset.id);
    const item = this.data.items.find((candidate) => Number(candidate.id) === id);
    const reason = String(this.data.reasonById[id] || "").trim();
    if (!item || reason.length < 2) {
      this.setData({ error: "请填写审核原因" });
      return;
    }
    withLoading(this, () => api.reviewIdentityRecoveryCase(id, {
      decision: event.currentTarget.dataset.decision,
      reason,
      expected_version: item.version,
    }).then(() => this.load())).catch(() => {});
  },
  finalize(event) {
    const id = Number(event.currentTarget.dataset.id);
    const item = this.data.items.find((candidate) => Number(candidate.id) === id);
    if (!item) return;
    withLoading(this, () => api.finalizeIdentityRecoveryCase(id, item.version).then(() => this.load())).catch(() => {});
  },
});
