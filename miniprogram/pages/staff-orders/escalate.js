const api = require('../../services/api');
Page({
  data: {
    workOrder: null, loading: false, error: '', reason: '', reasonIndex: 0,
    options: [
      { value: 'INTERNAL_UNRESOLVED', label: '无法解决，需要主管协助' },
      { value: 'WAITING_PARTS', label: '等待配件' },
      { value: 'WAITING_OWNER', label: '等待住户配合' },
      { value: 'ESCALATED', label: '其他异常' },
    ],
  },
  onLoad(query) { this.id = Number(query.id); },
  onShow() { this.hidden = false; return this.reload(); },
  onHide() { this.hidden = true; this.generation = (this.generation || 0) + 1; this.setData({ workOrder: null, reason: '', loading: false }); },
  onUnload() { this.onHide(); },
  async reload() {
    const generation = this.generation = (this.generation || 0) + 1;
    this.setData({ workOrder: null, loading: true, error: '' });
    // A read during a pending write can return the pre-submit version.
    // The submit completion reloads a visible page before enabling any action.
    if (this.busy) return;
    try {
      if (!Number.isSafeInteger(this.id) || this.id <= 0) throw new Error('工单编号无效');
      const item = await api.workOrder(this.id, 'property');
      if (generation !== this.generation) return;
      const order = item.work_order;
      if (!order || Number(order.id) !== this.id || order.status !== 'PROCESSING' ||
          order.process_reason !== 'NONE' || !Number.isSafeInteger(order.version)) {
        throw new Error('工单状态已变化，请返回查看最新进展');
      }
      this.setData({ workOrder: order });
    } catch (error) {
      if (generation === this.generation) this.setData({ error: error.message || '工单读取失败' });
    } finally {
      if (generation === this.generation) this.setData({ loading: false });
    }
  },
  onReason(event) { if (!this.data.loading) this.setData({ reason: event.detail.value }); },
  onType(event) {
    const index = Number(event.detail.value);
    if (!this.data.loading && Number.isInteger(index) && this.data.options[index]) this.setData({ reasonIndex: index });
  },
  async submit() {
    if (this.busy || this.data.loading || !this.data.workOrder) return;
    const reason = this.data.reason.trim();
    if (!reason) { this.setData({ error: '请填写无法处理或需要等待的原因' }); return; }
    const generation = this.generation;
    const order = this.data.workOrder;
    this.busy = true;
    this.setData({ loading: true, error: '' });
    try {
      await api.escalateWorkOrder(order.id, {
        process_reason: this.data.options[this.data.reasonIndex].value, reason, version: order.version,
      });
      if (generation !== this.generation) {
        if (!this.hidden) wx.showToast({ title: '已提交主管处理', icon: 'none' });
        return;
      }
      this.setData({ workOrder: null, reason: '' });
      wx.showToast({ title: '已提交主管处理', icon: 'none' });
      wx.navigateBack();
    } catch (error) {
      if (generation === this.generation) this.setData({ workOrder: null,
        error: error.message || '提交结果未确认，请刷新核对工单状态' });
    } finally {
      this.busy = false;
      if (generation === this.generation) this.setData({ loading: false });
      else if (!this.hidden) await this.reload();
    }
  },
});
