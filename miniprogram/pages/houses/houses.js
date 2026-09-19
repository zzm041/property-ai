Page({
  data: {
    items: [],
    requests: [],
    properties: [],
    buildings: [],
    units: [],
    houses: [],
    propertyNames: [],
    buildingNames: [],
    unitNames: [],
    houseNames: [],
    propertyIndex: 0,
    buildingIndex: 0,
    unitIndex: 0,
    houseIndex: 0,
    selectedHouseId: 0,
    loading: false,
    error: "",
  },
  onShow() {
    this.reload();
  },
  names(rows, key) {
    return (rows || []).map((item) => item[key]);
  },
  reload() {
    const { withLoading } = require("../../utils/ui");
    const api = require("../../services/api");
    withLoading(this, () =>
      Promise.all([api.houses(), api.myBindRequests(), api.catalogProperties()]).then(
        ([houses, requests, catalog]) => {
          const properties = (catalog && catalog.items) || [];
          this.setData({
            items: (houses && houses.items) || [],
            requests: (requests && requests.items) || [],
            properties,
            propertyNames: this.names(properties, "name"),
          });
          if (properties.length) {
            return this.loadBuildings(properties[0].id);
          }
          return null;
        }
      )
    ).catch(() => {});
  },
  loadBuildings(propertyId) {
    return require("../../services/api")
      .catalogBuildings(propertyId)
      .then((payload) => {
        const buildings = (payload && payload.items) || [];
        this.setData({
          buildings,
          buildingNames: this.names(buildings, "name"),
          buildingIndex: 0,
        });
        if (buildings.length) {
          return this.loadUnits(buildings[0].id);
        }
        this.setData({ units: [], houses: [], unitNames: [], houseNames: [], selectedHouseId: 0 });
        return null;
      });
  },
  loadUnits(buildingId) {
    return require("../../services/api")
      .catalogUnits(buildingId)
      .then((payload) => {
        const units = (payload && payload.items) || [];
        this.setData({ units, unitNames: this.names(units, "name"), unitIndex: 0 });
        if (units.length) {
          return this.loadHouses(units[0].id);
        }
        this.setData({ houses: [], houseNames: [], selectedHouseId: 0 });
        return null;
      });
  },
  loadHouses(unitId) {
    return require("../../services/api")
      .catalogHouses(unitId)
      .then((payload) => {
        const houses = (payload && payload.items) || [];
        this.setData({
          houses,
          houseNames: houses.map((item) => item.room_no),
          houseIndex: 0,
          selectedHouseId: houses[0] ? houses[0].id : 0,
        });
      });
  },
  onProperty(e) {
    const index = Number(e.detail.value);
    const item = this.data.properties[index];
    this.setData({ propertyIndex: index });
    if (item) {
      this.loadBuildings(item.id);
    }
  },
  onBuilding(e) {
    const index = Number(e.detail.value);
    const item = this.data.buildings[index];
    this.setData({ buildingIndex: index });
    if (item) {
      this.loadUnits(item.id);
    }
  },
  onUnit(e) {
    const index = Number(e.detail.value);
    const item = this.data.units[index];
    this.setData({ unitIndex: index });
    if (item) {
      this.loadHouses(item.id);
    }
  },
  onHousePick(e) {
    const index = Number(e.detail.value);
    const item = this.data.houses[index];
    this.setData({ houseIndex: index, selectedHouseId: item ? item.id : 0 });
  },
  retry() {
    this.reload();
  },
  selectHouse(e) {
    const houseId = e.currentTarget.dataset.id;
    require("../../services/auth").setSelectedHouseId(houseId);
    wx.navigateTo({ url: `/pages/work-orders/create?houseId=${houseId}` });
  },
  applyBind() {
    const houseId = Number(this.data.selectedHouseId);
    if (!houseId) {
      this.setData({ error: "请选择房号" });
      return;
    }
    const { withLoading } = require("../../utils/ui");
    withLoading(this, () =>
      require("../../services/api")
        .createBindRequest({ house_id: houseId, relation_type: "OWNER" })
        .then(() => this.reload())
    ).catch(() => {});
  },
});
