Component({
  properties: { workOrderId: { type: Number, observer: 'loadContact' } },
  data: { visible: false, collected: false, masked: '', phone: '', busy: false, error: '' },
  lifetimes: { detached() { this.generation = (this.generation || 0) + 1; } },
  pageLifetimes: { hide() { this.generation = (this.generation || 0) + 1; this.setData({ phone: '', busy: false }); }, show() { this.loadContact(); } },
  methods: {
    loadContact() {
      const id = this.properties.workOrderId;
      const generation = this.generation = (this.generation || 0) + 1;
      this.setData({ visible: false, collected: false, masked: '', phone: '', error: '', busy: false });
      if (!id) return Promise.resolve();
      return require('../../services/api').workOrderContact(id).then(result => {
        if (generation === this.generation) this.setData({ visible: true, collected: result.collected, masked: result.masked_phone || '' });
      }).catch(() => {}); // No unrelated user receives plaintext or a phone hint.
    },
    reveal() { return this.readContact(false); },
    call() { return this.readContact(true); },
    readContact(dial) {
      if (this.data.busy || !this.properties.workOrderId) return Promise.resolve();
      const generation = this.generation;
      this.setData({ busy: true, error: '', phone: '' });
      return require('../../services/api').workOrderContact(this.properties.workOrderId, true).then(result => {
        if (generation !== this.generation) return;
        if (!result.phone) throw new Error('历史工单未采集联系电话');
        if (dial) wx.makePhoneCall({ phoneNumber: result.phone, fail: () => { if (generation === this.generation) this.setData({ error: '拨号未完成' }); } });
        else this.setData({ phone: result.phone });
      }).catch(() => { if (generation === this.generation) this.setData({ error: '无权读取或读取失败，请刷新后重试' }); })
        .finally(() => { if (generation === this.generation) this.setData({ busy: false }); });
    },
  },
});
