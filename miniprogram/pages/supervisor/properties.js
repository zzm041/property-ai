const api = require("../../services/api");
const { withLoading } = require("../../utils/ui");

const IMPORT_COLUMNS = [
  "building_code",
  "building_name",
  "unit_code",
  "unit_name",
  "room_no",
  "floor",
  "status",
];

function scopeAllows(scope, permission, property) {
  if ((scope.permissions || []).indexOf(permission) < 0) {
    return false;
  }
  if (scope.scope_type === "SYSTEM") {
    return true;
  }
  if (Number(scope.tenant_id) !== Number(property.tenant_id)) {
    return false;
  }
  return (
    scope.scope_type === "TENANT" ||
    (scope.scope_type === "PROPERTY" &&
      Number(scope.property_id) === Number(property.id))
  );
}

function loadAllProperties(afterId, collected) {
  const current = collected || [];
  return api.catalogProperties(afterId || 0).then((response) => {
    const items = (response && response.items) || [];
    const next = current.concat(items);
    if (items.length < 100) {
      return next;
    }
    return loadAllProperties(items[items.length - 1].id, next);
  });
}

function loadAllRelations(propertyId, tenantId, afterId, collected) {
  const current = collected || [];
  return api
    .residentRelationHistory(propertyId, tenantId, afterId || 0)
    .then((response) => {
      const items = (response && response.items) || [];
      const next = current.concat(items);
      if (!(response && response.next_cursor)) {
        return next;
      }
      return loadAllRelations(propertyId, tenantId, response.next_cursor, next);
    });
}

function parseCsvLine(line) {
  const values = [];
  let value = "";
  let quoted = false;
  for (let index = 0; index < line.length; index += 1) {
    const character = line[index];
    if (character === '"') {
      if (quoted && line[index + 1] === '"') {
        value += '"';
        index += 1;
      } else {
        quoted = !quoted;
      }
    } else if (character === "," && !quoted) {
      values.push(value.trim());
      value = "";
    } else {
      value += character;
    }
  }
  if (quoted) {
    throw new Error("CSV 引号没有闭合");
  }
  values.push(value.trim());
  return values;
}

function parseImport(text) {
  const lines = String(text || "")
    .replace(/^\uFEFF/, "")
    .split(/\r?\n/)
    .filter((line) => line.trim());
  if (lines.length < 2) {
    throw new Error("请粘贴表头和至少一行房屋数据");
  }
  const header = parseCsvLine(lines[0]);
  if (header.join(",") !== IMPORT_COLUMNS.join(",")) {
    throw new Error(`CSV 表头必须为 ${IMPORT_COLUMNS.join(",")}`);
  }
  return lines.slice(1).map((line, index) => {
    const values = parseCsvLine(line);
    if (values.length !== IMPORT_COLUMNS.length) {
      throw new Error(`第 ${index + 2} 行列数不正确`);
    }
    const floor = values[5] === "" ? null : Number(values[5]);
    if (floor !== null && !Number.isInteger(floor)) {
      throw new Error(`第 ${index + 2} 行楼层必须为整数`);
    }
    const status = (values[6] || "ACTIVE").toUpperCase();
    if (["ACTIVE", "INACTIVE"].indexOf(status) < 0) {
      throw new Error(`第 ${index + 2} 行状态必须为 ACTIVE 或 INACTIVE`);
    }
    return {
      building_code: values[0],
      building_name: values[1],
      unit_code: values[2],
      unit_name: values[3],
      room_no: values[4],
      floor,
      status,
    };
  });
}

function csvValue(value) {
  if (value === null || value === undefined) {
    return "";
  }
  const text = String(value);
  return /[",\r\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

Page({
  data: {
    properties: [],
    propertyIndex: 0,
    propertyId: "",
    tenantId: "",
    canEdit: false,
    buildings: [],
    units: [],
    houses: [],
    relations: [],
    buildingOptions: [],
    unitOptions: [],
    buildingIndex: 0,
    unitIndex: 0,
    selectedBuilding: null,
    selectedBuildingId: "",
    editBuildingCode: "",
    editBuildingName: "",
    selectedUnit: null,
    selectedUnitId: "",
    editUnitCode: "",
    editUnitName: "",
    selectedHouse: null,
    selectedHouseId: "",
    selectedHouseStatusIndex: 0,
    houseStatusOptions: ["ACTIVE", "INACTIVE"],
    buildingCode: "",
    buildingName: "",
    unitCode: "",
    unitName: "",
    roomNo: "",
    floor: "",
    editRoomNo: "",
    editFloor: "",
    importText: "building_code,building_name,unit_code,unit_name,room_no,floor,status\n",
    loading: false,
    error: "",
    notice: "",
  },

  onLoad(query) {
    this.requestedPropertyId = query.propertyId || "";
  },

  onShow() {
    this.bootstrap();
  },

  bootstrap() {
    withLoading(this, () =>
      Promise.all([api.me(), loadAllProperties()]).then(([me, allProperties]) => {
        const scopes = me.scopes || [];
        const properties = allProperties
          .filter((item) =>
            scopes.some((scope) => scopeAllows(scope, "property:read", item))
          )
          .map((item) => ({
            ...item,
            canEdit: scopes.some((scope) =>
              scopeAllows(scope, "property:manage", item)
            ),
          }));
        if (!properties.length) {
          throw new Error("当前身份没有可查看的房产范围");
        }
        let propertyIndex = properties.findIndex(
          (item) => Number(item.id) === Number(this.requestedPropertyId)
        );
        if (propertyIndex < 0) {
          propertyIndex = 0;
        }
        const current = properties[propertyIndex];
        this.setData({
          properties,
          propertyIndex,
          propertyId: String(current.id),
          tenantId: String(current.tenant_id),
          canEdit: current.canEdit,
        });
        return this.loadData();
      })
    ).catch(() => {});
  },

  onPropertyChange(event) {
    const propertyIndex = Number(event.detail.value);
    const current = this.data.properties[propertyIndex];
    if (!current) {
      return;
    }
    this.setData({
      propertyIndex,
      propertyId: String(current.id),
      tenantId: String(current.tenant_id),
      canEdit: current.canEdit,
      selectedHouse: null,
      selectedHouseId: "",
      selectedBuilding: null,
      selectedBuildingId: "",
      selectedUnit: null,
      selectedUnitId: "",
      notice: "",
    });
    this.load();
  },

  load() {
    withLoading(this, () => this.loadData()).catch(() => {});
  },

  loadData() {
    const propertyId = this.data.propertyId;
    const tenantId = this.data.tenantId;
    return Promise.all([
      api.propertyArchive(propertyId, tenantId),
      loadAllRelations(propertyId, tenantId),
    ]).then(([archive, relationItems]) => {
      const buildings = (archive && archive.buildings) || [];
      const units = (archive && archive.units) || [];
      const buildingById = {};
      buildings.forEach((item) => {
        buildingById[item.id] = item;
      });
      const unitsWithLabel = units.map((item) => ({
        ...item,
        label: `${buildingById[item.building_id].code} / ${item.code} ${item.name}`,
      }));
      const unitById = {};
      unitsWithLabel.forEach((item) => {
        unitById[item.id] = item;
      });
      const houses = ((archive && archive.houses) || []).map((item) => {
        const unit = unitById[item.unit_id];
        const building = unit ? buildingById[unit.building_id] : null;
        return {
          ...item,
          buildingCode: building ? building.code : "?",
          unitCode: unit ? unit.code : "?",
          label: `${building ? building.code : "?"} / ${unit ? unit.code : "?"} / ${item.room_no}`,
        };
      });
      const houseById = {};
      houses.forEach((item) => {
        houseById[item.id] = item;
      });
      const relations = relationItems.map((item) => ({
        ...item,
        houseLabel: houseById[item.house_id]
          ? houseById[item.house_id].label
          : `房屋 ${item.house_id}`,
      }));
      this.setData({
        buildings,
        units: unitsWithLabel,
        houses,
        relations,
        buildingOptions: buildings,
        unitOptions: unitsWithLabel,
        buildingIndex: 0,
        unitIndex: 0,
        error: "",
      });
      if (this.data.selectedBuildingId) {
        this.selectBuildingById(this.data.selectedBuildingId, buildings);
      }
      if (this.data.selectedUnitId) {
        this.selectUnitById(this.data.selectedUnitId, unitsWithLabel);
      }
      if (this.data.selectedHouseId) {
        this.selectHouseById(this.data.selectedHouseId, houses);
      }
    });
  },

  onTextInput(event) {
    this.setData({ [event.currentTarget.dataset.field]: event.detail.value });
  },

  onBuildingChange(event) {
    this.setData({ buildingIndex: Number(event.detail.value) });
  },

  onUnitChange(event) {
    this.setData({ unitIndex: Number(event.detail.value) });
  },

  onHouseStatusChange(event) {
    this.setData({ selectedHouseStatusIndex: Number(event.detail.value) });
  },

  createBuilding() {
    if (!this.data.buildingCode.trim() || !this.data.buildingName.trim()) {
      this.setData({ error: "请填写楼栋编码和名称" });
      return;
    }
    withLoading(this, () =>
      api
        .createBuilding({
          tenant_id: Number(this.data.tenantId),
          property_id: Number(this.data.propertyId),
          code: this.data.buildingCode.trim(),
          name: this.data.buildingName.trim(),
        })
        .then((building) => {
          this.setData({
            buildingCode: "",
            buildingName: "",
            selectedBuildingId: String(building.id),
            notice: "楼栋已建立",
          });
          return this.loadData();
        })
    ).catch(() => {});
  },

  createUnit() {
    const building = this.data.buildingOptions[this.data.buildingIndex];
    if (!building || !this.data.unitCode.trim() || !this.data.unitName.trim()) {
      this.setData({ error: "请选择楼栋并填写单元编码和名称" });
      return;
    }
    withLoading(this, () =>
      api
        .createUnit({
          tenant_id: Number(this.data.tenantId),
          property_id: Number(this.data.propertyId),
          building_id: Number(building.id),
          code: this.data.unitCode.trim(),
          name: this.data.unitName.trim(),
        })
        .then((unit) => {
          this.setData({
            unitCode: "",
            unitName: "",
            selectedUnitId: String(unit.id),
            notice: "单元已建立",
          });
          return this.loadData();
        })
    ).catch(() => {});
  },

  selectBuilding(event) {
    this.selectBuildingById(event.currentTarget.dataset.id);
  },

  selectBuildingById(buildingId, supplied) {
    const buildings = supplied || this.data.buildings;
    const building = buildings.find((item) => Number(item.id) === Number(buildingId));
    if (!building) {
      this.setData({ selectedBuilding: null, selectedBuildingId: "" });
      return;
    }
    const buildingIndex = this.data.buildingOptions.findIndex(
      (item) => Number(item.id) === Number(building.id)
    );
    this.setData({
      selectedBuilding: building,
      selectedBuildingId: String(building.id),
      editBuildingCode: building.code,
      editBuildingName: building.name,
      buildingIndex: buildingIndex < 0 ? 0 : buildingIndex,
    });
  },

  updateBuilding() {
    if (
      !this.data.selectedBuilding ||
      !this.data.editBuildingCode.trim() ||
      !this.data.editBuildingName.trim()
    ) {
      this.setData({ error: "请填写有效的楼栋编码和名称" });
      return;
    }
    withLoading(this, () =>
      api
        .updateBuilding(this.data.selectedBuilding.id, {
          code: this.data.editBuildingCode.trim(),
          name: this.data.editBuildingName.trim(),
        })
        .then(() => {
          this.setData({ notice: "楼栋档案已更新" });
          return this.loadData();
        })
    ).catch(() => {});
  },

  selectUnit(event) {
    this.selectUnitById(event.currentTarget.dataset.id);
  },

  selectUnitById(unitId, supplied) {
    const units = supplied || this.data.units;
    const unit = units.find((item) => Number(item.id) === Number(unitId));
    if (!unit) {
      this.setData({ selectedUnit: null, selectedUnitId: "" });
      return;
    }
    const unitIndex = this.data.unitOptions.findIndex(
      (item) => Number(item.id) === Number(unit.id)
    );
    const buildingIndex = this.data.buildingOptions.findIndex(
      (item) => Number(item.id) === Number(unit.building_id)
    );
    this.setData({
      selectedUnit: unit,
      selectedUnitId: String(unit.id),
      editUnitCode: unit.code,
      editUnitName: unit.name,
      unitIndex: unitIndex < 0 ? 0 : unitIndex,
      buildingIndex: buildingIndex < 0 ? this.data.buildingIndex : buildingIndex,
    });
  },

  updateUnit() {
    if (
      !this.data.selectedUnit ||
      !this.data.editUnitCode.trim() ||
      !this.data.editUnitName.trim()
    ) {
      this.setData({ error: "请填写有效的单元编码和名称" });
      return;
    }
    withLoading(this, () =>
      api
        .updateUnit(this.data.selectedUnit.id, {
          code: this.data.editUnitCode.trim(),
          name: this.data.editUnitName.trim(),
        })
        .then(() => {
          this.setData({ notice: "单元档案已更新" });
          return this.loadData();
        })
    ).catch(() => {});
  },

  createHouse() {
    const unit = this.data.unitOptions[this.data.unitIndex];
    const floor = this.data.floor.trim() === "" ? null : Number(this.data.floor);
    if (!unit || !this.data.roomNo.trim() || (floor !== null && !Number.isInteger(floor))) {
      this.setData({ error: "请选择单元并填写有效房号和楼层" });
      return;
    }
    withLoading(this, () =>
      api
        .createHouse({
          tenant_id: Number(this.data.tenantId),
          property_id: Number(this.data.propertyId),
          unit_id: Number(unit.id),
          room_no: this.data.roomNo.trim(),
          floor,
          status: "ACTIVE",
        })
        .then((house) => {
          this.setData({
            roomNo: "",
            floor: "",
            selectedHouseId: String(house.id),
            notice: "房屋档案已建立",
          });
          return this.loadData();
        })
    ).catch(() => {});
  },

  selectHouse(event) {
    this.selectHouseById(event.currentTarget.dataset.id);
  },

  selectHouseById(houseId, supplied) {
    const house = (supplied || this.data.houses).find(
      (item) => Number(item.id) === Number(houseId)
    );
    if (!house) {
      this.setData({ selectedHouse: null, selectedHouseId: "" });
      return;
    }
    let statusIndex = this.data.houseStatusOptions.indexOf(house.status);
    if (statusIndex < 0) {
      statusIndex = 0;
    }
    this.setData({
      selectedHouse: house,
      selectedHouseId: String(house.id),
      selectedHouseStatusIndex: statusIndex,
      editRoomNo: house.room_no,
      editFloor: house.floor === null ? "" : String(house.floor),
    });
  },

  updateHouse() {
    if (!this.data.selectedHouse) {
      return;
    }
    const floor = this.data.editFloor.trim() === "" ? null : Number(this.data.editFloor);
    if (!this.data.editRoomNo.trim() || (floor !== null && !Number.isInteger(floor))) {
      this.setData({ error: "请填写有效房号和楼层" });
      return;
    }
    withLoading(this, () =>
      api
        .updateHouse(this.data.selectedHouse.id, {
          room_no: this.data.editRoomNo.trim(),
          floor,
          status: this.data.houseStatusOptions[this.data.selectedHouseStatusIndex],
        })
        .then(() => {
          this.setData({ notice: "房屋档案已更新" });
          return this.loadData();
        })
    ).catch(() => {});
  },

  importHouses() {
    let rows;
    try {
      rows = parseImport(this.data.importText);
    } catch (error) {
      this.setData({ error: error.message });
      return;
    }
    withLoading(this, () =>
      api
        .importHouses({
          tenant_id: Number(this.data.tenantId),
          property_id: Number(this.data.propertyId),
          rows,
        })
        .then((result) => {
          this.setData({
            notice: `导入完成：楼栋 ${result.created_buildings}，单元 ${result.created_units}，房屋 ${result.created_houses}，未变化 ${result.unchanged_houses}`,
          });
          return this.loadData();
        })
    ).catch(() => {});
  },

  exportHouses() {
    withLoading(this, () =>
      api.exportHouses(this.data.propertyId, this.data.tenantId).then((result) => {
        const lines = [result.columns.map(csvValue).join(",")].concat(
          (result.rows || []).map((row) =>
            result.columns.map((column) => csvValue(row[column])).join(",")
          )
        );
        return new Promise((resolve, reject) => {
          wx.setClipboardData({
            data: lines.join("\r\n"),
            success: () => {
              this.setData({ notice: `已导出并复制 ${result.rows.length} 行 CSV` });
              resolve();
            },
            fail: () => reject(new Error("复制导出内容失败")),
          });
        });
      })
    ).catch(() => {});
  },

  endRelation(event) {
    const relationId = event.currentTarget.dataset.id;
    wx.showModal({
      title: "结束住户关系",
      content: "结束后会保留完整历史，住户将不再以此关系访问房屋。",
      success: (result) => {
        if (!result.confirm) {
          return;
        }
        withLoading(this, () =>
          api.endResidentRelation(relationId, "ENDED").then(() => {
            this.setData({ notice: "住户关系已结束，历史记录已保留" });
            return this.loadData();
          })
        ).catch(() => {});
      },
    });
  },
});
