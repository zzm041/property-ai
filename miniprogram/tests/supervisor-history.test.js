const assert = require('assert'), fs = require('fs'), path = require('path'), vm = require('vm');
const flush = async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); };
function setup() {
  let definition; const calls = [], pending = [];
  vm.runInNewContext(fs.readFileSync(path.resolve(__dirname, '../pages/supervisor/queue.js'), 'utf8'), {
    Page(value) { definition = value; },
    require(name) {
      if (name === '../../services/api') return { workOrder(id, audience) {
        calls.push([id, audience]); return new Promise((resolve, reject) => pending.push({resolve,reject}));
      } };
      if (name === '../../utils/work-order-display') return require('../utils/work-order-display');
      throw new Error(name);
    }, wx: {},
  });
  const page = { ...definition, data: JSON.parse(JSON.stringify(definition.data)),
    setData(data) { Object.assign(this.data, data); } };
  page.setData({ selectedId: '31', propertyId: '9', selectedVersion: 3 });
  return { page, calls, pending };
}
const item = { work_order: { id: 31, property_id: 9, version: 3 },
  text: '镜柜门合不上。请先在小程序留言。',
  asr_text: '收到我的回复后再电话联系；未经我确认不要上门。',
  extra_notes: '合成累计处理说明', logs: [{id: 4, event_code: 'DECIDE', reason: '合成联系安排'}] };
(async () => {
  const c = setup(); c.page.loadInternalHistory(); await flush();
  assert.deepEqual(c.calls, [[31, 'property']]);
  c.pending[0].resolve(item); await flush();
  assert.equal(c.page.data.internalHistory.logs[0].reason, '合成联系安排');
  assert.equal(c.page.data.internalHistory.notes, item.extra_notes);
  assert.equal(c.page.data.internalHistory.reportText, item.text);
  assert.equal(c.page.data.internalHistory.asrText, item.asr_text);
  c.page.loadInternalHistory(); await flush();
  assert.equal(c.page.data.internalHistory, null);
  c.pending[1].reject(new Error('permission removed')); await flush();
  assert.equal(c.page.data.internalHistory, null);
  for (const mismatch of [{ id: 99 }, {property_id: 10}, {version: 4}]) {
    c.page.loadInternalHistory(); await flush();
    c.pending.at(-1).resolve({...item,work_order:{...item.work_order,...mismatch}}); await flush();
    assert.equal(c.page.data.internalHistory, null);
  }
  for (const change of ['hide','selection','diagnosis']) {
    const count = c.pending.length;
    c.page.loadInternalHistory(); await flush();
    assert.equal(c.pending.length, count + 1);
    if (change === 'hide') c.page.onHide();
    if (change === 'selection') c.page._clearSelection();
    if (change === 'diagnosis') c.page.clearDiagnosis();
    c.pending.at(-1).resolve(item); await flush();
    assert.equal(c.page.data.internalHistory, null);
    c.page.setData({selectedId:'31',propertyId:'9',selectedVersion:3});
  }
  for (const fields of [
    { text: null, asr_text: '仅语音：晚上八点后先留言，确认后再上门。' },
    { text: '仅文字：窗户关不上。', asr_text: null },
    { text: null, asr_text: null },
  ]) {
    c.page.loadInternalHistory(); await flush();
    c.pending.at(-1).resolve({...item, ...fields}); await flush();
    assert.equal(c.page.data.internalHistory.reportText, fields.text || '');
    assert.equal(c.page.data.internalHistory.asrText, fields.asr_text || '');
  }
  console.log('supervisor internal history, purpose, scope, version and stale response contracts passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
