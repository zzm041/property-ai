const assert = require('assert/strict');
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const source = fs.readFileSync(path.resolve(__dirname, '../pages/assistant/assistant.js'), 'utf8');
const flush = () => new Promise((resolve) => setImmediate(resolve));
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
function setup() {
  let page;
  const calls = [], pending = [];
  const api = {
    queryAssistant(body) { calls.push(body); const next = deferred(); pending.push(next); return next.promise; },
    me: () => Promise.resolve({ scopes: [{ scope_type: 'PROPERTY', tenant_id: 7, property_id: 9, permissions: ['knowledge:read'] }] }),
    catalogProperties: () => Promise.resolve({ items: [{ id: 9, tenant_id: 7, name: 'synthetic' }] }),
  };
  vm.runInNewContext(source, {
    Page: (definition) => { page = definition; }, module: { exports: {} },
    require: (name) => name.endsWith('/api') ? api : require('../utils/ui'),
  });
  page.data = JSON.parse(JSON.stringify(page.data));
  page.setData = (values) => Object.assign(page.data, values);
  page.onLoad({});
  page.setData({ propertyId: '9', question: 'synthetic', properties: [{ id: 9 }, { id: 10 }] });
  return { page, api, calls, pending };
}
const internal = { answer: 'internal synthetic', status: 'answered', citations: [{ document_id: 31 }] };
(async () => {
  for (const missing of ['question', 'propertyId']) {
    const c = setup();
    c.page.setData({ answer: internal.answer, citations: internal.citations, [missing]: '' });
    c.page.ask(); await flush();
    assert.equal(c.page.data.answer, '', `local validation must clear old answer: ${missing}`);
    assert.equal(c.page.data.citations.length, 0);
    assert.equal(c.calls.length, 0);
  }
  {
    const c = setup(); c.page.ask(); await flush();
    c.page.setData({ question: '' }); c.page.ask();
    c.pending[0].resolve(internal); await flush();
    assert.equal(c.page.data.answer, '', 'invalid new query must invalidate pending old answer');
  }
  {
    const c = setup(); c.page.ask();
    c.page.onAudienceChange({ detail: { value: '1' } });
    await flush();
    assert.equal(c.calls.length, 0, 'cancelled public query must not become an internal query before dispatch');
  }
  {
    const c = setup(); c.page.ask(); await flush();
    assert.equal(c.calls[0].audience, 'resident');
    c.pending[0].resolve(internal); await flush();
    c.page.onAudienceChange({ detail: { value: '1' } });
    assert.equal(c.page.data.answer, ''); assert.equal(c.page.data.citations.length, 0);
    c.page.ask(); await flush(); assert.equal(c.calls[1].audience, 'property');
    c.pending[1].resolve(internal); await flush();
  }
  for (const action of ['audience', 'property', 'hide', 'show']) {
    const c = setup(); c.page.onAudienceChange({ detail: { value: '1' } });
    c.page.ask(); await flush();
    if (action === 'audience') c.page.onAudienceChange({ detail: { value: '0' } });
    if (action === 'property') c.page.onPropertyChange({ detail: { value: '1' } });
    if (action === 'hide') c.page.onHide();
    if (action === 'show') c.page.onShow();
    await flush(); c.pending[0].resolve(internal); await flush();
    assert.equal(c.page.data.answer, '', `stale answer after ${action}`);
    assert.equal(c.page.data.citations.length, 0);
  }
  {
    const c = setup(); c.page.setData({ answer: internal.answer, citations: internal.citations });
    c.page.ask(); await flush();
    assert.equal(c.page.data.answer, '');
    c.pending[0].reject(new Error('forbidden')); await flush();
    assert.equal(c.page.data.answer, ''); assert.equal(c.page.data.citations.length, 0);
    assert.equal(c.page.data.error, 'forbidden');
  }
  {
    const c = setup(); c.page.ask(); await flush();
    c.page.onAudienceChange({ detail: { value: '1' } });
    c.page.ask(); await flush();
    c.pending[0].reject(new Error('old failure')); await flush();
    assert.equal(c.page.data.error, ''); assert.equal(c.page.data.loading, true);
    c.pending[1].resolve(internal); await flush();
    assert.equal(c.page.data.answer, internal.answer); assert.equal(c.page.data.loading, false);
  }
  {
    const c = setup(); c.page.setData({ answer: internal.answer, citations: internal.citations });
    c.api.me = () => Promise.reject(new Error('expired'));
    c.page.onShow(); await flush();
    assert.equal(c.page.data.propertyId, ''); assert.equal(c.page.data.answer, '');
    c.page.ask(); await flush(); assert.equal(c.calls.length, 0);
  }
  console.log('assistant audience, stale responses, reentry and failure contracts passed');
})().catch((error) => { console.error(error); process.exitCode = 1; });
