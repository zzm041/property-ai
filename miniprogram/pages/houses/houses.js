const RELATIONS = [
  { value: "OWNER", label: "业主" },
  { value: "TENANT", label: "租户" },
  { value: "FAMILY", label: "家属" },
  { value: "AUTHORIZED_OTHER", label: "其他授权住户" },
];
const STATUS_NAMES = { PENDING: "待审核", APPROVED: "已通过", REJECTED: "已驳回" };
const withRelationLabel = (item) => ({
  ...item,
  relationLabel: (RELATIONS.find((option) => option.value === item.relation_type) || {}).label || item.relation_type,
  statusLabel: STATUS_NAMES[item.status] || item.status,
});

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
    focusedRequestId: 0,
    relationNames: RELATIONS.map((item) => item.label),
    relationIndex: -1,
    catalogLoading: false,
    loading: false,
    error: "",
  },
  onLoad(query) {
    const requestId = Number(query.requestId || 0);
    if (Number.isInteger(requestId) && requestId > 0) {
      this.setData({ focusedRequestId: requestId });
    }
  },
  onShow() {
    this.reload();
  },
  onUnload() {
    this.catalogVersion = (this.catalogVersion || 0) + 1;
  },
  names(rows, key) {
    return (rows || []).map((item) => item[key]);
  },
  reload() {
    if (this.data.loading) return Promise.resolve();
    const { withLoading } = require("../../utils/ui");
    return withLoading(this, () => this.loadPage()).catch(() => {});
  },
  loadPage() {
    const api = require("../../services/api");
    const version = this.clearSelection("property");
    this.setData({ properties: [], propertyNames: [], propertyIndex: 0 });
    return Promise.all([api.houses(), this.loadBindRequests(), api.catalogProperties()]).then(
        ([houses, requests, catalog]) => {
          if (version !== this.catalogVersion) return;
          const properties = (catalog && catalog.items) || [];
          this.setData({
            items: ((houses && houses.items) || []).map(withRelationLabel),
            requests: requests.map(withRelationLabel),
            properties,
            propertyNames: this.names(properties, "name"),
          });
          if (properties.length) {
            return this.loadBuildings(properties[0].id, version);
          }
          return null;
        }
    ).finally(() => {
      if (version === this.catalogVersion) this.setData({ catalogLoading: false });
    });
  },
  loadBindRequests(afterId = 0, existing = [], page = 0) {
    if (page >= 20) {
      throw new Error("申请记录过多，请稍后重试");
    }
    return require("../../services/api")
      .myBindRequests(afterId, 100)
      .then((payload) => {
        const incoming = (payload && payload.items) || [];
        const byId = {};
        const items = existing.concat(incoming).filter((item) => {
          const key = String(item.id);
          if (byId[key]) {
            return false;
          }
          byId[key] = true;
          return true;
        });
        const nextCursor = Number((payload && payload.next_cursor) || 0);
        if (!nextCursor) {
          return items;
        }
        if (nextCursor <= Number(afterId || 0)) {
          throw new Error("申请记录分页游标无效");
        }
        return this.loadBindRequests(nextCursor, items, page + 1);
      });
  },
  clearSelection(level) {
    this.catalogVersion = (this.catalogVersion || 0) + 1;
    const values = { houses: [], houseNames: [], houseIndex: 0, selectedHouseId: 0, catalogLoading: true, error: "" };
    if (level !== "unit") Object.assign(values, { units: [], unitNames: [], unitIndex: 0 });
    if (level === "property") Object.assign(values, { buildings: [], buildingNames: [], buildingIndex: 0 });
    this.setData(values);
    return this.catalogVersion;
  },
  changeAddress(level, work) {
    if (this.data.loading) return Promise.resolve();
    const version = this.clearSelection(level);
    return Promise.resolve().then(() => work(version)).catch((error) => {
      if (version === this.catalogVersion) this.setData({ error: error.message || "地址加载失败，请重新选择" });
    }).finally(() => {
      if (version === this.catalogVersion) this.setData({ catalogLoading: false });
    });
  },
  loadBuildings(propertyId, version) {
    return require("../../services/api")
      .catalogBuildings(propertyId)
      .then((payload) => {
        if (version !== this.catalogVersion) return;
        const buildings = (payload && payload.items) || [];
        this.setData({
          buildings,
          buildingNames: this.names(buildings, "name"),
          buildingIndex: 0,
        });
        if (buildings.length) {
          return this.loadUnits(buildings[0].id, version);
        }
        this.setData({ units: [], houses: [], unitNames: [], houseNames: [], selectedHouseId: 0 });
        return null;
      });
  },
  loadUnits(buildingId, version) {
    return require("../../services/api")
      .catalogUnits(buildingId)
      .then((payload) => {
        if (version !== this.catalogVersion) return;
        const units = (payload && payload.items) || [];
        this.setData({ units, unitNames: this.names(units, "name"), unitIndex: 0 });
        if (units.length) {
          return this.loadHouses(units[0].id, version);
        }
        this.setData({ houses: [], houseNames: [], selectedHouseId: 0 });
        return null;
      });
  },
  loadHouses(unitId, version) {
    return require("../../services/api")
      .catalogHouses(unitId)
      .then((payload) => {
        if (version !== this.catalogVersion) return;
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
    if (this.data.loading) return;
    const index = Number(e.detail.value);
    const item = this.data.properties[index];
    this.setData({ propertyIndex: index });
    return this.changeAddress("property", (version) => item && this.loadBuildings(item.id, version));
  },
  onBuilding(e) {
    if (this.data.loading) return;
    const index = Number(e.detail.value);
    const item = this.data.buildings[index];
    this.setData({ buildingIndex: index });
    return this.changeAddress("building", (version) => item && this.loadUnits(item.id, version));
  },
  onUnit(e) {
    if (this.data.loading) return;
    const index = Number(e.detail.value);
    const item = this.data.units[index];
    this.setData({ unitIndex: index });
    return this.changeAddress("unit", (version) => item && this.loadHouses(item.id, version));
  },
  onHousePick(e) {
    if (this.data.loading || this.data.catalogLoading) return;
    const index = Number(e.detail.value);
    const item = this.data.houses[index];
    this.setData({ houseIndex: index, selectedHouseId: item ? item.id : 0 });
  },
  onRelation(e) {
    if (this.data.loading) return;
    const index = Number(e.detail.value);
    this.setData({ relationIndex: RELATIONS[index] ? index : -1 });
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
    if (this.data.loading || this.data.catalogLoading) return;
    const houseId = Number(this.data.selectedHouseId);
    if (!houseId) {
      this.setData({ error: "请选择房号" });
      return;
    }
    const relation = RELATIONS[this.data.relationIndex];
    if (!relation) {
      this.setData({ error: "请选择与房屋的关系" });
      return;
    }
    const { withLoading } = require("../../utils/ui");
    return withLoading(this, () =>
      require("../../services/api")
        .createBindRequest({ house_id: houseId, relation_type: relation.value })
        .then(() => this.loadPage())
    ).catch(() => {});
  },
});
