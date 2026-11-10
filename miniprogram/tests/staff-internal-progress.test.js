const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const flush = async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); };
function setup() {
  let definition;
  const pending = [], calls = [];
  const api = { me() { return Promise.resolve({ user_id: 7 }); }, workOrder(id, audience) {
    calls.push([id, audience]);
    return new Promise((resolve, reject) => pending.push({ resolve, reject }));
  } };
  vm.runInNewContext(fs.readFileSync(path.resolve(__dirname, '../pages/staff-orders/detail.js'), 'utf8'), {
    Page(value) { definition = value; },
    require(name) {
      if (name === '../../services/api') return api;
      if (name === '../../services/auth') return { setLastWorkOrderId() {} };
      if (name === '../../utils/ui') return require('../utils/ui');
      if (name === '../../utils/work-order-display') return require('../utils/work-order-display');
      throw new Error(name);
    }, wx: {},
  });
  const page = { ...definition, data: JSON.parse(JSON.stringify(definition.data)),
    setData(data) { Object.assign(this.data, data); } };
  page.onLoad({ id: '31' });
  return { page, pending, calls };
}
const detail = { work_order: { id: 31, status: 'PROCESSING', process_reason: 'NONE' },
  text: '合成报修：柜门合不上。', asr_text: '请先留言，回复后再电话联系。', extra_notes: '未经确认不要上门。',
  logs: [{ id: 9, event_code: 'DECIDE', actor_type: 'SUPERVISOR', reason: '合成人工联系安排与下一步' }] };
(async () => {
  const c = setup();
  c.page.onShow(); await flush();
  assert.deepEqual(c.calls, [[31, 'property']]);
  c.pending[0].resolve(detail); await flush();
  assert.equal(c.page.data.logs[0].reason, detail.logs[0].reason);
  assert.equal(c.page.data.item.text, detail.text);
  assert.equal(c.page.data.item.asr_text, detail.asr_text);
  assert.equal(c.page.data.item.extra_notes, detail.extra_notes);
  c.page.onHide();
  assert.equal(c.page.data.item, null); assert.equal(c.page.data.logs.length, 0);
  assert.equal(c.page.data.canFinish, false);
  c.page.onShow(); await flush();
  c.pending[1].reject(new Error('permission removed')); await flush();
  assert.equal(c.page.data.logs.length, 0); assert.equal(c.page.data.error, 'permission removed');
  c.page.reload(); await flush(); c.page.reload(); await flush();
  c.pending[3].resolve({ ...detail, logs: [] }); await flush();
  c.pending[2].resolve(detail); await flush();
  assert.equal(c.page.data.logs.length, 0);
  c.page.reload(); await flush(); c.page.onHide();
  c.pending[4].resolve(detail); await flush();
  assert.equal(c.page.data.item, null);
  c.page.onShow(); await flush();
  c.pending[5].resolve({ ...detail, work_order: { ...detail.work_order, id: 77 } }); await flush();
  assert.equal(c.page.data.item, null); assert.equal(c.page.data.logs.length, 0);
  const template = fs.readFileSync(path.resolve(__dirname, '../pages/staff-orders/detail.wxml'), 'utf8');
  assert.match(template, /\{\{item\.reason\}\}/);
  const history = setup(); history.page.onShow(); await flush();
  const business = Array.from({length:23}, (_,i) => ({id:i+1,event_code:'PROCESS_UPDATE',actor_type:'STAFF',actor_user_id:7}));
  const system = Array.from({length:159}, (_,i) => ({id:i+100,event_code:'AUTO_DISPATCH_WAIT',actor_type:'SYSTEM'}));
  history.pending[0].resolve({...detail, logs:[...business,...system]}); await flush();
  assert.equal(history.page.data.logs.length,10,'initial business history must be bounded');
  assert.equal(history.page.data.logs[0].id,23,'latest progress must be first');
  assert.equal(history.page.data.logs[0].actorLabel,'本人');
  assert.equal(history.page.data.systemLogs.length,0,'diagnostics must be collapsed initially');
  assert.equal(history.page.data.systemLogCount,159);
  history.page.showMoreLogs(); assert.equal(history.page.data.logs.length,20);
  history.page.showMoreLogs(); assert.equal(history.page.data.logs.length,23);
  history.page.toggleSystemLogs(); assert.equal(history.page.data.systemLogs.length,10);
  history.page.showMoreSystemLogs(); assert.equal(history.page.data.systemLogs.length,20);
  history.page.onHide(); assert.equal(history.page.data.systemLogs.length,0);
  history.page.showMoreLogs(); history.page.toggleSystemLogs();
  assert.equal(history.page.data.logs.length,0); assert.equal(history.page.data.systemLogs.length,0);
  console.log('staff internal progress, authorization refresh and stale response contracts passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
