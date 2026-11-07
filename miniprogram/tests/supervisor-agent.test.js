const assert = require('assert/strict');
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const flush = () => new Promise(resolve => setImmediate(resolve));
function setup() {
  let page;
  const calls = [], pending = [], mutations = [];
  const api = {
    supervisorAgent(propertyId, question) {
      calls.push({ propertyId, question });
      return new Promise((resolve, reject) => pending.push({ resolve, reject }));
    },
    supervisorAgentCapabilities: () => Promise.resolve({ enabled: true }),
    supervisorQueue: () => Promise.resolve({ items: [] }),
    staffDaily: () => Promise.resolve({ items: [] }),
    employees: () => Promise.resolve({ items: [] }),
  };
  for (const method of ['assignWorkOrder', 'reassignWorkOrder', 'decideWorkOrder', 'cancelWorkOrder', 'analyzeWorkOrder']) {
    api[method] = () => new Promise((resolve, reject) => mutations.push({ resolve, reject }));
  }
  vm.runInNewContext(fs.readFileSync(path.resolve(__dirname, '../pages/supervisor/queue.js'), 'utf8'), {
    Page: definition => { page = definition; },
    require: name => name.endsWith('/api') ? api : require(name.replace('../../', '../')),
  });
  page.data = JSON.parse(JSON.stringify(page.data));
  page.setData = value => Object.assign(page.data, value);
  page.onLoad({});
  page.setData({ propertyId: '9', tenantId: '7', agentAvailable: true,
    properties: [{ id: 9, tenant_id: 7 }, { id: 10, tenant_id: 7 }],
    items: [{ id: 11, version: 2, status: 'PENDING_ASSIGNMENT' }] });
  return { page, calls, pending, mutations };
}
const result = { status: 'completed', property_id: 9, execution_mode: 'deterministic_stub', model_calls: 0,
  items: [{ work_order_id: 11, version: 2, facts: [{ code: 'CURRENT_STATE', text: '等待处理' }], suggested_actions: ['核实情况'] }] };
(async () => {
  {
    const c = setup(); c.page.reviewQueue(); c.page.reviewQueue(); await flush();
    assert.equal(c.calls.length, 1);
    assert.equal(c.calls[0].propertyId, 9);
    c.pending[0].resolve(result); await flush();
    assert.equal(c.page.data.agentResult.items[0].work_order_id, 11);
    assert.equal(c.page.data.agentLoading, false);
  }
  for (const change of ['property', 'hide', 'refresh']) {
    const c = setup(); c.page.reviewQueue(); await flush();
    if (change === 'property') c.page.onPropertyChange({ detail: { value: '1' } });
    if (change === 'hide') c.page.onHide();
    if (change === 'refresh') c.page.load();
    c.pending[0].resolve(result); await flush();
    assert.equal(c.page.data.agentResult, null, change);
  }
  for (const altered of [
    { ...result, property_id: 10 },
    { ...result, items: [{ ...result.items[0], version: 3 }] },
    { ...result, status: 'unavailable', items: [] },
  ]) {
    const c = setup(); c.page.reviewQueue(); await flush();
    c.pending[0].resolve(altered); await flush();
    assert.equal(c.page.data.agentResult, null);
    assert.ok(c.page.data.agentError);
  }
  {
    const c = setup(); c.page.setData({ agentAvailable: false }); c.page.reviewQueue();
    assert.equal(c.calls.length, 0);
  }
  for (const action of ['assign', 'reassign', 'decide', 'cancelSelected', 'recommend']) {
    for (const alreadyVisible of [false, true]) {
      const c = setup(); c.page.reviewQueue(); await flush();
      if (alreadyVisible) { c.pending[0].resolve(result); await flush(); }
      c.page.setData({ selectedId: 11, selectedVersion: 2, employeeId: '5',
        selectedStatus: action === 'decide' ? 'PROCESSING' : 'PENDING_ASSIGNMENT',
        decisionReason: '核实处理', canCancel: true, cancelConfirm: true });
      if (action === 'decide') c.page.decide({ currentTarget: { dataset: { decision: 'WAIT_PARTS' } } });
      else c.page[action]();
      await flush();
      assert.equal(c.mutations.length, 1, action);
      assert.equal(c.page.data.agentResult, null, action + ' clears existing result');
      if (!alreadyVisible) { c.pending[0].resolve(result); await flush(); }
      assert.equal(c.page.data.agentResult, null, action + ' discards old response');
      c.mutations[0].reject(new Error('network outcome unknown')); await flush();
      assert.equal(c.page.data.agentResult, null, action + ' does not revive after failure');
    }
  }
  console.log('supervisor agent: duplicate clicks, scope, stale responses, failure and disabled entry passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
