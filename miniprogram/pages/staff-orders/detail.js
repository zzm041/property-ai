Page({
  data: { item: null, error: "" },
  onLoad(query) { this.id = Number(query.id); this.reload(); },
  reload() {
    require("../../services/api").workOrder(this.id)
      .then((item) => this.setData({ item }))
      .catch((error) => this.setData({ error: error.message }));
  },
  accept() { this._act("acceptWorkOrder"); },
  start() { this._act("startWorkOrder"); },
  _act(name) {
    require("../../services/api")[name](this.id).then(() => this.reload())
      .catch((error) => this.setData({ error: error.message }));
  },
});
