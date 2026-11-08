const assert = require('assert'), fs = require('fs'), path = require('path'), vm = require('vm');
const flush = async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); };
function setup() {
  let definition; const scopes=[], queues=[], analyses=[], recommendations=[];
  const deferred = list => new Promise((resolve,reject)=>list.push({resolve,reject}));
  const api = { supervisorQueue:()=>deferred(queues), staffDaily:async()=>({items:[{employee_id:3,on_duty:'ON_DUTY'}]}),
    analyzeWorkOrder:()=>deferred(analyses), workOrderRecommendations:()=>deferred(recommendations) };
  vm.runInNewContext(fs.readFileSync(path.resolve(__dirname,'../pages/supervisor/queue.js'),'utf8'),{
    Page(value){definition=value;}, require(name){
      if(name==='../../services/api')return api;
      if(name==='../../utils/property-scope')return {authorizedProperties:()=>deferred(scopes),preferredIndex:()=>0};
      if(name==='../../utils/ui')return require('../utils/ui');
      if(name==='../../utils/business-labels')return require('../utils/business-labels');
      throw new Error(name);
    },wx:{},
  });
  const page={...definition,data:JSON.parse(JSON.stringify(definition.data)),setData(data){Object.assign(this.data,data);}};
  return {page,scopes,queues,analyses,recommendations};
}
const properties=[{id:9,tenant_id:2,name:'synthetic'}];
const queue={items:[{id:31,property_id:9,tenant_id:2,status:'PENDING_ASSIGNMENT',process_reason:'NONE',version:3}]};
(async()=>{
  const a=setup();a.page.onShow();await flush();a.page.onHide();a.scopes[0].resolve(properties);await flush();
  assert.equal(a.queues.length,0,'hidden scope callback must not fetch queue');
  assert.equal(a.page.data.properties.length,0);
  const b=setup();b.page.onShow();await flush();b.scopes[0].resolve(properties);await flush();
  assert.equal(b.queues.length,1);b.page.onHide();b.queues[0].resolve(queue);await flush();
  assert.equal(b.page.data.items.length,0);assert.equal(b.page.data.staff.length,0);
  const c=setup();c.page.onShow();await flush();c.scopes[0].resolve(properties);await flush();c.queues[0].resolve(queue);await flush();
  assert.equal(c.page.data.items.length,1);assert.equal(c.page.data.staff.length,1);
  c.page.onShow();await flush();
  assert.equal(c.page.data.items.length,0);assert.equal(c.page.data.staff.length,0);
  c.scopes[1].reject(new Error('scope revoked'));await flush();
  assert.equal(c.page.data.properties.length,0);assert.equal(c.page.data.items.length,0);
  assert.equal(c.page.data.error,'scope revoked');
  const d=setup();d.page.setData({selectedId:31});d.page.recommend();await flush();
  assert.equal(d.analyses.length,1);d.page.onHide();d.analyses[0].resolve({});await flush();
  assert.equal(d.recommendations.length,0,'do not fetch recommendations after leaving');
  const e=setup();e.page.setData({selectedId:31});e.page.recommend();await flush();e.analyses[0].resolve({});await flush();
  assert.equal(e.recommendations.length,1);e.page.onHide();
  e.recommendations[0].resolve({items:[{employee_id:3,eligible:true}]});await flush();
  assert.equal(e.page.data.recommendations.length,0,'do not restore private recommendation data');
  console.log('supervisor visibility-cycle scope and queue stale response contracts passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
