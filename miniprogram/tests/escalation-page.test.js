const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const root = path.resolve(__dirname, '..');
const template = fs.readFileSync(path.join(root, 'pages/staff-orders/detail.wxml'), 'utf8');
assert(!template.includes('bindtap="escalate"'), 'exception form must leave the receipt page');
assert(template.indexOf('bindtap="openEscalation"') > template.indexOf('bindtap="resolve"'));
assert(require('../app.json').pages.includes('pages/staff-orders/escalate'));
let definition, writes = [], back = 0, load, submit;
const api = { workOrder() { return load(); }, escalateWorkOrder(id, body) { writes.push([id, body]); return submit(); } };
vm.runInNewContext(fs.readFileSync(path.join(root, 'pages/staff-orders/escalate.js'), 'utf8'), {
  Page(value) { definition = value; }, require() { return api; }, wx: { showToast() {}, navigateBack() { back++; } },
});
const fresh = () => ({ ...definition, data: JSON.parse(JSON.stringify(definition.data)), setData(v) { Object.assign(this.data, v); } });
const order = { work_order: { id: 22, status: 'PROCESSING', process_reason: 'NONE', version: 8 } };
(async () => {
  let page = fresh(); page.onLoad({ id: '22' }); load = async () => order; await page.onShow();
  await page.submit(); assert.equal(writes.length, 0); assert.match(page.data.error, /原因/);
  page.onReason({ detail: { value: '缺少所需配件' } });
  let finish; submit = () => new Promise(resolve => { finish = resolve; });
  const pending = page.submit(); await page.submit(); assert.equal(writes.length, 1);
  assert.equal(writes[0][1].version, 8); finish({}); await pending; assert.equal(back, 1);
  page = fresh(); page.onLoad({ id: '22' });
  let resolveLoad; load = () => new Promise(resolve => { resolveLoad = resolve; });
  const delayed = page.onShow(); page.onHide(); resolveLoad(order); await delayed;
  assert.equal(page.data.workOrder, null);
  load = async () => { throw new Error('无权查看'); }; await page.onShow(); assert.equal(page.data.workOrder, null);
  load = async () => ({ work_order: { ...order.work_order, status: 'COMPLETED' } }); await page.onShow();
  assert.equal(page.data.workOrder, null);
  load = async () => order; await page.onShow(); page.onReason({ detail: { value: '缺少配件' } });
  submit = async () => { throw new Error('状态已变更，请刷新'); }; await page.submit();
  assert.equal(page.data.workOrder, null); assert.match(page.data.error, /刷新/);
  // Returning from the background must not expose the pre-submit version.
  page = fresh(); page.onLoad({ id: '22' }); load = async () => order; await page.onShow();
  page.onReason({ detail: { value: '等待配件' } });
  submit = () => new Promise(resolve => { finish = resolve; });
  const backgroundSubmit = page.submit(); page.onHide(); await page.onShow();
  assert.equal(page.data.workOrder, null, 'in-flight write must keep old form unavailable');
  load = async () => ({ work_order: { ...order.work_order, version: 9, process_reason: 'WAITING_PARTS' } });
  finish({}); await backgroundSubmit;
  assert.equal(page.data.workOrder, null);
  console.log('Separate exception page: required reason, duplicate submit, stale/denied/changed state passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
