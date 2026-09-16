Page({
  data: { items: [], error: "", propertyId: "" },
  onProperty(e) { this.setData({ propertyId: e.detail.value }); },
  load() {
    require("../../services/api").request({
      path: `/admin/resident-bind-requests?property_id=${this.data.propertyId}`,
    }).then((data) => this.setData({ items: data.items || [] }))
      .catch((error) => this.setData({ error: error.message }));
  },
});
