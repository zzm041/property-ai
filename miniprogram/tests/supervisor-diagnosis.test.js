const assert = require('assert/strict');
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const flush = () => new Promise(resolve => setImmediate(resolve));
function setup() {
  let page;
  const calls = [], pending = [];
  let analysisCalls = 0;
  const api = {
    workOrderDiagnosis(...args) { calls.push(args); return new Promise((resolve, reject) => pending.push({ resolve, reject })); },
    analyzeWorkOrder() { analysisCalls++; return Promise.resolve(); },
    supervisorQueue: () => Promise.resolve({ items: [] }),
    staffDaily: () => Promise.resolve({ items: [] }),
    employees: () => Promise.resolve({ items: [] }),
  };
  vm.runInNewContext(fs.readFileSync(path.resolve(__dirname, '../pages/supervisor/queue.js'), 'utf8'), {
    Page: definition => { page = definition; },
    require: name => name.endsWith('/api') ? api : require(name.replace('../../', '../')),
  });
  page.data = JSON.parse(JSON.stringify(page.data));
  page.setData = values => Object.assign(page.data, values);
  page.onLoad({});
  page.setData({ propertyId: '9', tenantId: '7', properties: [{ id: 9, tenant_id: 7 }, { id: 10, tenant_id: 7 }],
    items: [{ id: 31, version: 3, status: 'PENDING_ASSIGNMENT' }, { id: 41, version: 1, status: 'PROCESSING' }] });
  page.select({ currentTarget: { dataset: { id: 31 } } });
  return { page, calls, pending, analysisCalls: () => analysisCalls };
}
const result = { work_order_id: 31, property_id: 9, version: 3, observed_at: '2026-11-01T12:00:00Z',
  facts: [{ code: 'WAITING_IDLE', text: 'historical wait', source_type: 'work_order_log', source_id: 71 }],
  suggested_actions: ['check current eligibility'] };
(async () => {
  {
    const c = setup(); c.page.diagnose(); await flush();
    assert.deepEqual(c.calls[0], [31, 9, 3]);
    c.pending[0].resolve(result); await flush();
    assert.equal(c.page.data.diagnosis.facts[0].text, 'historical wait');
    assert.equal(c.analysisCalls(), 0);
  }
  for (const change of ['select', 'property', 'hide', 'refresh']) {
    const c = setup(); c.page.diagnose(); await flush();
    if (change === 'select') c.page.select({ currentTarget: { dataset: { id: 41 } } });
    if (change === 'property') c.page.onPropertyChange({ detail: { value: '1' } });
    if (change === 'hide') c.page.onHide();
    if (change === 'refresh') c.page.load();
    c.pending[0].resolve(result); await flush();
    assert.equal(c.page.data.diagnosis, null, change);
  }
  {
    const c = setup(); c.page.diagnose(); await flush();
    c.pending[0].resolve(result); await flush();
    c.page.diagnose(); await flush();
    assert.equal(c.page.data.diagnosis, null);
    c.pending[1].reject(new Error('version changed')); await flush();
    assert.equal(c.page.data.diagnosis, null);
    assert.equal(c.page.data.diagnosisError, 'version changed');
  }
  {
    const c = setup(); c.page.diagnose(); await flush();
    c.pending[0].resolve({ ...result, work_order_id: 41 }); await flush();
    assert.equal(c.page.data.diagnosis, null);
  }
  console.log('supervisor diagnosis readonly, version, scope and stale-response contracts passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
