Page({
  data: { propertyId: "", snapshot: null, error: "" },
  onProperty(e) { this.setData({ propertyId: e.detail.value }); },
  checkIn() {
    require("../../services/api").checkIn(Number(this.data.propertyId))
      .then((snapshot) => this.setData({ snapshot }))
      .catch((error) => this.setData({ error: error.message }));
  },
  checkOut() {
    require("../../services/api").checkOut(Number(this.data.propertyId))
      .then((snapshot) => this.setData({ snapshot }))
      .catch((error) => this.setData({ error: error.message }));
  },
});
