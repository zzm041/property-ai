const RELATIONS = [
  { value: "OWNER", label: "业主" },
  { value: "TENANT", label: "租户" },
  { value: "FAMILY", label: "家属" },
  { value: "AUTHORIZED_OTHER", label: "其他授权住户" },
];
const STATUS_NAMES = { PENDING: "待审核", APPROVED: "已通过", REJECTED: "已驳回" };
const { appendPage } = require("../../utils/paging");
const PAGE_SIZE = 50;
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
    moreLoading: false,
    propertyCursor: 0,
    buildingCursor: 0,
    unitCursor: 0,
    houseCursor: 0,
    activeCursor: 0,
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
    if (this.data.loading || this.data.moreLoading) return Promise.resolve();
    const { withLoading } = require("../../utils/ui");
    return withLoading(this, () => this.loadPage()).catch(() => {});
  },
  loadPage() {
    const api = require("../../services/api");
    const version = this.clearSelection("property");
    this.setData({ properties: [], propertyNames: [], propertyIndex: 0, propertyCursor: 0, activeCursor: 0 });
    return Promise.all([api.houses(0, PAGE_SIZE), this.loadBindRequests(), api.catalogProperties(0, PAGE_SIZE)]).then(
        ([houses, requests, catalog]) => {
          if (version !== this.catalogVersion) return;
          const catalogPage = appendPage([], catalog, 0, PAGE_SIZE);
          const activePage = appendPage([], houses, 0, PAGE_SIZE, "relation_id");
          const properties = catalogPage.items;
          this.setData({
            items: activePage.items.map(withRelationLabel),
            activeCursor: activePage.nextCursor,
            propertyCursor: catalogPage.nextCursor,
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
    const values = { houses: [], houseNames: [], houseIndex: 0, houseCursor: 0, selectedHouseId: 0, catalogLoading: true, error: "" };
    if (level !== "unit") Object.assign(values, { units: [], unitNames: [], unitIndex: 0, unitCursor: 0 });
    if (level === "property") Object.assign(values, { buildings: [], buildingNames: [], buildingIndex: 0, buildingCursor: 0 });
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
        const result = appendPage([], payload, 0, PAGE_SIZE);
        const buildings = result.items;
        this.setData({
          buildings,
          buildingNames: this.names(buildings, "name"),
          buildingIndex: 0,
          buildingCursor: result.nextCursor,
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
        const result = appendPage([], payload, 0, PAGE_SIZE);
        const units = result.items;
        this.setData({ units, unitNames: this.names(units, "name"), unitIndex: 0, unitCursor: result.nextCursor });
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
        const result = appendPage([], payload, 0, PAGE_SIZE);
        const houses = result.items;
        this.setData({
          houses,
          houseNames: houses.map((item) => item.room_no),
          houseIndex: 0,
          houseCursor: result.nextCursor,
          selectedHouseId: houses[0] ? houses[0].id : 0,
        });
      });
  },
  loadMoreCatalog(e) {
    const level = e.currentTarget.dataset.level;
    const config = {
      property: ["properties", "propertyNames", "catalogProperties", null, "name"],
      building: ["buildings", "buildingNames", "catalogBuildings", this.data.properties[this.data.propertyIndex], "name"],
      unit: ["units", "unitNames", "catalogUnits", this.data.buildings[this.data.buildingIndex], "name"],
      house: ["houses", "houseNames", "catalogHouses", this.data.units[this.data.unitIndex], "room_no"],
    }[level];
    if (!config || this.data.loading || this.data.catalogLoading || this.data.moreLoading) return;
    const cursorKey = level + "Cursor";
    const after = this.data[cursorKey];
    if (!after || (level !== "property" && !config[3])) return;
    const version = this.catalogVersion;
    const api = require("../../services/api");
    this.setData({ moreLoading: true, error: "" });
    return Promise.resolve().then(() => level === "property"
      ? api[config[2]](after, PAGE_SIZE)
      : api[config[2]](config[3].id, after, PAGE_SIZE)
    ).then((payload) => {
      if (version !== this.catalogVersion) return;
      const result = appendPage(this.data[config[0]], payload, after, PAGE_SIZE);
      this.setData({ [config[0]]: result.items, [config[1]]: this.names(result.items, config[4]), [cursorKey]: result.nextCursor });
    }).catch((error) => {
      if (version === this.catalogVersion) this.setData({ error: error.message || "加载失败" });
    }).finally(() => this.setData({ moreLoading: false }));
  },
  loadMoreActive() {
    if (this.data.loading || this.data.catalogLoading || this.data.moreLoading || !this.data.activeCursor) return;
    const after = this.data.activeCursor;
    this.setData({ moreLoading: true, error: "" });
    return require("../../services/api").houses(after, PAGE_SIZE).then((payload) => {
      const result = appendPage(this.data.items, payload, after, PAGE_SIZE, "relation_id");
      this.setData({ items: result.items.map(withRelationLabel), activeCursor: result.nextCursor });
    }).catch((error) => this.setData({ error: error.message || "加载失败" }))
      .finally(() => this.setData({ moreLoading: false }));
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
    if (this.data.loading || this.data.catalogLoading || this.data.moreLoading) return;
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
    if (this.data.loading || this.data.catalogLoading || this.data.moreLoading) return;
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
