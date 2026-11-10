const emptyStatus = () => ({ snapshot: null, canCheckIn: false, canCheckOut: false,
  checkInLabel: "", checkOutLabel: "", onDutyLabel: "", attendanceLabel: "" });

Page({
  data: { properties: [], propertyIndex: 0, propertyId: "", ...emptyStatus(), loading: false, actionBusy: false, error: "" },
  onLoad(query) { this.requestedPropertyId = query.propertyId || ""; },
  onShow() {
    this._hidden = false;
    if (this._actionPending) { this.setData({ loading: true }); return Promise.resolve(); }
    return this.reload();
  },
  onHide() {
    this._hidden = true;
    this._generation = (this._generation || 0) + 1;
    this.setData({ ...emptyStatus(), properties: [], error: "", loading: false });
  },
  onUnload() { this.onHide(); },
  isCurrent(generation) { return !this._hidden && generation === this._generation; },
  reload() {
    if (this._hidden || this._actionPending) return Promise.resolve();
    const generation = this._generation = (this._generation || 0) + 1;
    const preferred = this.data.propertyId || this.requestedPropertyId;
    this.setData({ ...emptyStatus(), properties: [], loading: true, error: "" });
    const api = require("../../services/api");
    const scope = require("../../utils/property-scope");
    return scope.authorizedProperties(api, "attendance:check_in").then((properties) => {
      if (!this.isCurrent(generation)) return;
      if (!properties.length) throw new Error("当前身份没有可签到的小区");
      const propertyIndex = scope.preferredIndex(properties, preferred);
      const propertyId = Number(properties[propertyIndex].id);
      this.setData({ properties, propertyIndex, propertyId: String(propertyId) });
      return this.readToday(propertyId, generation);
    }).catch((error) => {
      if (this.isCurrent(generation)) this.setData({ ...emptyStatus(), properties: [], error: error.message || "读取今日状态失败" });
    }).finally(() => { if (this.isCurrent(generation)) this.setData({ loading: false }); });
  },
  readToday(propertyId, generation) {
    return require("../../services/api").attendanceToday(propertyId).then((snapshot) => {
      if (this.isCurrent(generation)) this.applySnapshot(snapshot, propertyId);
    });
  },
  applySnapshot(snapshot, propertyId) {
    if (!snapshot || !snapshot.business_date || [snapshot.attendance, snapshot.daily_status]
      .some((row) => row && Number(row.property_id) !== propertyId)) throw new Error("签到状态与当前小区不一致，请刷新");
    const record = snapshot.attendance;
    const active = Boolean(record && !record.check_out_at);
    const { formatBeijingTime } = require("../../utils/date-time");
    const { onDutyStatus } = require("../../utils/business-labels");
    this.setData({ snapshot, canCheckIn: !active, canCheckOut: active,
      attendanceLabel: !record ? "今日未签到" : active ? "已签到" : "已签退",
      onDutyLabel: snapshot.daily_status ? onDutyStatus(snapshot.daily_status.on_duty) : "未在岗",
      checkInLabel: record ? formatBeijingTime(record.check_in_at) : "尚未签到",
      checkOutLabel: record && record.check_out_at ? formatBeijingTime(record.check_out_at) : "尚未签退" });
  },
  onPropertyChange(e) {
    if (this._hidden || this._actionPending) return Promise.resolve();
    const propertyIndex = Number(e.detail.value), property = this.data.properties[propertyIndex];
    if (!property) return Promise.resolve();
    const generation = this._generation = (this._generation || 0) + 1;
    const propertyId = Number(property.id);
    this.setData({ ...emptyStatus(), propertyIndex, propertyId: String(propertyId), loading: true, error: "" });
    return this.readToday(propertyId, generation).catch((error) => {
      if (this.isCurrent(generation)) this.setData({ ...emptyStatus(), error: error.message || "读取今日状态失败" });
    }).finally(() => { if (this.isCurrent(generation)) this.setData({ loading: false }); });
  },
  checkIn() { return this.submit("checkIn", "canCheckIn"); },
  checkOut() { return this.submit("checkOut", "canCheckOut"); },
  submit(method, allowed) {
    if (this._hidden || this._actionPending || this.data.loading || !this.data[allowed]) return Promise.resolve();
    const propertyId = Number(this.data.propertyId), generation = this._generation;
    this._actionPending = true;
    this.setData({ actionBusy: true, error: "" });
    return Promise.resolve().then(() => require("../../services/api")[method](propertyId))
      .then((snapshot) => { if (this.isCurrent(generation)) this.applySnapshot(snapshot, propertyId); })
      .catch((error) => {
        if (this.isCurrent(generation)) this.setData({ ...emptyStatus(), error: error.message || "操作未确认，请刷新核实当前状态" });
      }).finally(() => {
        this._actionPending = false;
        this.setData({ actionBusy: false });
        if (!this._hidden && !this.isCurrent(generation)) return this.reload();
      });
  },
});
