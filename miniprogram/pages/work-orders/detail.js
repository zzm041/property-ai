Page({
  data: { item: null, error: "" },
  onLoad(query) {
    this.id = Number(query.id);
    this.reload();
  },
  reload() {
    const api = require("../../services/api");
    api.workOrder(this.id)
      .then((item) => this.setData({ item }))
      .catch((error) => this.setData({ error: error.message }));
  },
  confirm() {
    require("../../services/api").confirmWorkOrder(this.id).then(() => this.reload())
      .catch((error) => this.setData({ error: error.message }));
  },
  reopen() {
    require("../../services/api").reopenWorkOrder(this.id, { reason: "未解决" })
      .then(() => this.reload())
      .catch((error) => this.setData({ error: error.message }));
  },
});
