const assert = require('assert/strict');
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const flush = () => new Promise(resolve => setImmediate(resolve));
function setup(kind = 'list') {
  let page;
  const pending = [], calls = [], navigations = [];
  function request(...args) {
    calls.push(args);
    return new Promise((resolve, reject) => pending.push({ resolve, reject }));
  }
  const api = {
    announcements: request, announcement: request,
    me: () => Promise.resolve({ scopes: [{ role_code: 'SUPERVISOR', scope_type: 'PROPERTY',
      tenant_id: 7, property_id: 9, permissions: ['announcement:read'] },
      { role_code: 'RESIDENT', scope_type: 'PROPERTY', tenant_id: 7, property_id: 10,
        permissions: ['announcement:read'] }] }),
    catalogProperties: () => Promise.resolve({ items: [{ id: 9, tenant_id: 7 }, { id: 10, tenant_id: 7 }] }),
  };
  vm.runInNewContext(fs.readFileSync(path.resolve(__dirname, `../pages/announcements/${kind}.js`), 'utf8'), {
    Page: definition => { page = definition; },
    wx: { navigateTo: value => navigations.push(value.url) },
    require: name => name.endsWith('/api') ? api : require(name.replace('../../', '../')),
  });
  page.data = JSON.parse(JSON.stringify(page.data));
  page.setData = values => Object.assign(page.data, values);
  page.onLoad(kind === 'list' ? {} : { id: '31', propertyId: '9', audience: 'property' });
  return { page, api, pending, calls, navigations };
}
async function boot(c) {
  c.page.onShow(); await flush();
  c.pending[0].resolve({ items: [{ id: 31, title: 'public', status: 'PUBLISHED' }] }); await flush();
}
(async () => {
  {
    const c=setup();c.page.onShow();await flush();
    c.pending[0].resolve({items:Array.from({length:50},(_,i)=>({id:i+1,status:'PUBLISHED'})),next_cursor:50});await flush();
    assert.equal(typeof c.page.loadMore,'function','announcements after the first 50 must be reachable');
    c.page.loadMore();await flush();assert.equal(c.calls[1][1],50);
    c.pending[1].resolve({items:[{id:51,status:'PUBLISHED'}],next_cursor:null});await flush();
    assert.equal(c.page.data.items.length,51);assert.equal(c.page.data.hasMore,false);
  }
  {
    const c=setup();c.page.onShow();await flush();
    c.pending[0].resolve({items:[{id:50,status:'PUBLISHED'}],next_cursor:50});await flush();
    c.page.loadMore();await flush();c.pending[1].reject(Error('分页网络失败'));await flush();
    assert.equal(c.page.data.items.length,1);assert.equal(c.page.data.hasMore,true);
    c.page.loadMore();await flush();c.page.onHide();c.pending[2].resolve({items:[{id:51}],next_cursor:null});await flush();
    assert.equal(c.page.data.items.length,0);
  }
  {
    const c=setup();c.page.onLoad({audience:'property',propertyId:'9'});
    c.page.onShow();await flush();
    assert.equal(c.calls[0][3],'property');assert.equal(c.page.data.audienceIndex,1);
    c.pending[0].resolve({items:[{id:31,title:'internal'}]});await flush();
    c.page.onHide();c.page.onShow();await flush();assert.equal(c.calls[1][3],'property');
    c.pending[1].resolve({items:[]});await flush();
    c.page.onPropertyChange({detail:{value:'1'}});await flush();
    assert.equal(c.page.data.items.length,0);assert.equal(c.calls.length,2);
    assert.match(c.page.data.error,/权限|范围/);
  }
  {
    const c=setup();c.page.onLoad({audience:'property',propertyId:'10'});
    c.page.onShow();await flush();assert.equal(c.calls.length,0);
    assert.equal(c.page.data.items.length,0);assert.match(c.page.data.error,/权限|范围/);
  }
  {
    const c = setup(); await boot(c);
    assert.equal(c.calls[0][3], 'resident');
    c.page.onAudienceChange({ detail: { value: '1' } }); await flush();
    assert.equal(c.page.data.items.length, 0);
    assert.equal(c.calls[1][3], 'property');
    c.pending[1].resolve({ items: [{ id: 31, title: 'internal' }] }); await flush();
    c.page.open({ currentTarget: { dataset: { id: 31 } } });
    assert.match(c.navigations[0], /audience=property/);
  }
  for (const change of ['audience', 'property', 'hide', 'show']) {
    const c = setup(); await boot(c);
    c.page.onAudienceChange({ detail: { value: '1' } }); await flush();
    if (change === 'audience') c.page.onAudienceChange({ detail: { value: '0' } });
    if (change === 'property') c.page.onPropertyChange({ detail: { value: '1' } });
    if (change === 'hide') c.page.onHide();
    if (change === 'show') c.page.onShow();
    await flush();
    c.pending[1].resolve({ items: [{ title: 'stale internal' }] }); await flush();
    assert.equal(c.page.data.items.length, 0, change);
  }
  {
    const c = setup(); await boot(c);
    c.page.load(); await flush();
    c.pending[1].reject(new Error('forbidden')); await flush();
    assert.equal(c.page.data.items.length, 0);
    assert.equal(c.page.data.error, 'forbidden');
  }
  {
    const c = setup('detail'); c.page.onShow(); await flush();
    assert.equal(c.calls[0][2], 'property');
    c.page.onHide();
    c.pending[0].resolve({ title: 'stale title', content: 'stale body' }); await flush();
    assert.equal(c.page.data.content, '');
    c.page.onShow(); await flush();
    c.pending[1].resolve({ title: 'current title', content: 'current body' }); await flush();
    assert.equal(c.page.data.content, 'current body');
    c.page.reload(); await flush();
    assert.equal(c.page.data.content, '');
    c.pending[2].reject(new Error('revoked')); await flush();
    assert.equal(c.page.data.content, '');
  }
  {
    const c = setup('detail');
    c.page.onLoad({ id: '31', propertyId: '9' });
    c.page.onShow(); await flush();
    assert.equal(c.calls[0][2], 'resident');
    c.pending[0].reject(new Error('resident has no binding')); await flush();
    c.page.onAudienceChange({ detail: { value: '1' } }); await flush();
    assert.equal(c.calls[1][2], 'property');
    c.pending[1].resolve({ title: 'internal', content: 'allowed staff body' }); await flush();
    assert.equal(c.page.data.content, 'allowed staff body');
  }
  console.log('announcement audience, scope switching, stale responses and detail contracts passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
